"""
The one way the app runs yt-dlp: probe-link (``link_probe_worker.py``, in a
child process) and the worker's download (``download_from_youtube``).

yt-dlp is built to fetch whatever a page points to, so on its own it is a
server-side request forgery: YouTube's consent page hands its ``continue``
URL to any of ~1,800 extractors, and an HTTP redirect can send a request to
the internal network. Here:

- only the extractors of the allowed sites run (``ALLOWED_EXTRACTORS``, the
  ``IE_NAME`` of each; no generic one, no consent page, no channel or list);
- the first extraction is ``process=False``, and a result that points to
  another link is followed only through those extractors, forced by key,
  after the link passes ``validate_video_link`` again;
- the result must be one video that is not live; only then is it processed
  (formats chosen and, for the download, downloaded);
- every request yt-dlp makes (``GuardedYoutubeDL.urlopen``) and every
  redirect it follows (``install_redirect_guard``) goes only to public
  addresses: an IP literal or a host that resolves to loopback, a private
  range, link-local or reserved is refused before connecting. Only the
  ``Urllib`` and ``Requests`` handlers are kept, the ones whose redirects are
  guarded.

What is still open: the lookup here and the connection are separate (a host
that changes its answer in between), and ffmpeg, which yt-dlp may run for a
stream it cannot download natively, makes its own requests. An outbound
firewall for the workers closes both.

Plain Python on purpose: the probe's child imports it without Django.
"""
import os

try:
    from . import link_validation
except ImportError:  # run by link_probe_worker.py as a script, without the package
    import link_validation

import yt_dlp
from yt_dlp.networking.exceptions import RequestError

# Same request identity for the probe and the download.
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)
REFERER = 'https://www.google.com/'
SOCKET_TIMEOUT_SECONDS = 10

# IE_NAME (what ``allowed_extractors`` matches) of the one extractor per kind
# of single video of LINK_HOSTS. ``validate_video_link`` turns every allowed
# link into a URL one of them reads (youtu.be into watch?v=, a Twitch clip
# page into clips.twitch.tv), so no other is needed.
ALLOWED_EXTRACTORS = ['youtube', 'vimeo', 'twitch:vod', 'twitch:clips', 'googledrive']
# The same extractors by ie_key, for the results that point to another link.
FOLLOW_EXTRACTORS = frozenset({'Youtube', 'Vimeo', 'TwitchVod', 'TwitchClips', 'GoogleDrive'})
MAX_HOPS = 2
LIVE_STATUSES = frozenset({'is_live', 'is_upcoming', 'post_live'})
_GUARDED_HANDLERS = frozenset({'Urllib', 'Requests'})


class BlockedRequest(RequestError):
    """yt-dlp was about to request a non-public address (or a scheme other than http/https)."""

    def __init__(self):
        super().__init__('blocked non-public address')


class LinkRefused(Exception):
    """
    The link is not one video the app downloads. ``code``: ``unsupported`` (a
    page that points off the allowed sites or extractors), ``not_a_video``
    (a list, a channel, no duration) or ``live``.
    """

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def check_public_url(url):
    """Raises BlockedRequest unless ``url`` is http(s) to a public address."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(url)
        port = parts.port
    except (ValueError, TypeError):
        raise BlockedRequest() from None
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
        raise BlockedRequest()
    try:
        link_validation.check_public_host(parts.hostname, port or (443 if parts.scheme.lower() == 'https' else 80))
    except (link_validation.LinkRejected, OSError, UnicodeError, ValueError):
        raise BlockedRequest() from None


_redirect_guard_installed = False


def install_redirect_guard():
    """
    Checks the target of every redirect yt-dlp's handlers follow. Idempotent;
    it only refuses non-public targets, so it is safe for the whole process.
    """
    global _redirect_guard_installed
    if _redirect_guard_installed:
        return
    from yt_dlp.networking import _urllib

    follow = _urllib.RedirectHandler.redirect_request

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public_url(newurl)
        return follow(self, req, fp, code, msg, headers, newurl)

    _urllib.RedirectHandler.redirect_request = redirect_request
    try:
        from yt_dlp.networking import _requests
    except ImportError:  # requests not installed: urllib is the only handler
        pass
    else:
        rebuild = _requests.RequestsSession.rebuild_method

        def rebuild_method(self, prepared_request, response):
            # requests already set the redirect target on prepared_request.url.
            check_public_url(prepared_request.url)
            return rebuild(self, prepared_request, response)

        _requests.RequestsSession.rebuild_method = rebuild_method
    _redirect_guard_installed = True


class GuardedYoutubeDL(yt_dlp.YoutubeDL):
    """A YoutubeDL whose every request and redirect goes to a public address."""

    def __init__(self, params=None, *args, **kwargs):
        install_redirect_guard()
        super().__init__(params, *args, **kwargs)
        director = getattr(self, '_request_director', None)
        handlers = getattr(director, 'handlers', None)
        if isinstance(handlers, dict):
            # Keep only the handlers whose redirects are guarded (no curl_cffi, no websockets).
            for key in [key for key in handlers if key not in _GUARDED_HANDLERS]:
                handlers.pop(key).close()

    def urlopen(self, req):
        check_public_url(req if isinstance(req, str) else getattr(req, 'url', getattr(req, 'full_url', '')))
        return super().urlopen(req)


class _SilentLogger:
    """yt-dlp writes errors to stderr: the provider's text stays out of the logs."""

    def __init__(self):
        self.saw_max_filesize = False

    def debug(self, msg):
        if 'max-filesize' in str(msg):
            self.saw_max_filesize = True

    info = warning = error = debug


def base_options():
    return {
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
        'noplaylist': True,
        'socket_timeout': SOCKET_TIMEOUT_SECONDS,
        'allowed_extractors': list(ALLOWED_EXTRACTORS),
        'user_agent': USER_AGENT,
        'referer': REFERER,
        'logger': _SilentLogger(),
        # Native downloaders go through urlopen (guarded); ffmpeg would not.
        'hls_prefer_native': True,
    }


def _merge(outer, inner):
    """url_transparent: the outer result's fields win, as yt-dlp does when it processes it."""
    extra = {key: value for key, value in outer.items() if key not in ('_type', 'url', 'ie_key') and value is not None}
    return {**inner, **extra}


def extract_single_video(ydl, url):
    """
    The info dict of the one video ``url`` names, not processed. Raises
    LinkRefused, or yt-dlp's DownloadError (the provider's text is in it:
    never log it).
    """
    info = ydl.extract_info(url, download=False, process=False)
    for _hop in range(MAX_HOPS + 1):
        if not isinstance(info, dict):
            raise LinkRefused('not_a_video')
        kind = info.get('_type') or 'video'
        if kind == 'video':
            break
        if kind not in ('url', 'url_transparent'):
            raise LinkRefused('not_a_video')  # playlist, multi_video, compat_list
        ie_key, target = info.get('ie_key'), info.get('url')
        if ie_key not in FOLLOW_EXTRACTORS or not isinstance(target, str):
            raise LinkRefused('unsupported')
        try:
            link_validation.validate_video_link(target, resolve=True)
        except (link_validation.LinkRejected, OSError):
            raise LinkRefused('unsupported') from None
        inner = ydl.extract_info(target, download=False, process=False, ie_key=ie_key)
        info = _merge(info, inner) if kind == 'url_transparent' and isinstance(inner, dict) else inner
    else:
        raise LinkRefused('unsupported')
    if info.get('is_live') or info.get('live_status') in LIVE_STATUSES:
        raise LinkRefused('live')
    # Processed with download=True, these would be extracted with no checks.
    info.pop('additional_urls', None)
    return info


class LinkTooLarge(Exception):
    """The download went over ``max_filesize``."""


def download_video(url, output_path, *, max_filesize, check_info=None):
    """
    Downloads the one video ``url`` names to ``output_path``. ``check_info``
    (the info dict) may raise before anything is downloaded (the duration of
    the plan). Raises LinkRefused, LinkTooLarge or yt-dlp's DownloadError.
    """
    def cap(progress):
        done = progress.get('downloaded_bytes') or 0
        expected = progress.get('total_bytes') or progress.get('total_bytes_estimate') or 0
        if max(done, expected) > max_filesize:
            raise LinkTooLarge()

    options = {
        **base_options(),
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': output_path,
        'max_filesize': max_filesize,
        'progress_hooks': [cap],
    }
    with GuardedYoutubeDL(options) as ydl:
        info = extract_single_video(ydl, url)
        if check_info is not None:
            check_info(info)
        ydl.process_ie_result(info, download=True)
    if not os.path.exists(output_path):
        if options['logger'].saw_max_filesize:
            raise LinkTooLarge()
        raise LinkRefused('not_a_video')
    if os.path.getsize(output_path) > max_filesize:
        os.remove(output_path)
        raise LinkTooLarge()
    return output_path
