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
  redirect it follows (``install_guards``) goes only to public addresses: an
  IP literal or a host that resolves to loopback, a private range,
  link-local or reserved is refused before connecting;
- and the address the socket actually connects to is checked again right
  before ``connect`` (``_guarded_create_connection``), so a name that
  answers public to the check and internal to the connection (DNS
  rebinding) is refused too. Only yt-dlp's ``Urllib`` handler is kept: its
  connections go through that function. A configured proxy is the one
  private address allowed (the proxy resolves the target itself).

Still open, for the infrastructure: ffmpeg, which yt-dlp may run for a
stream it cannot download natively, makes its own requests (an outbound
firewall on the workers closes it).

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
# The handler whose connections go through _guarded_create_connection.
_GUARDED_HANDLERS = frozenset({'Urllib'})
# Hosts of the proxies the GuardedYoutubeDL instances use: the only names a
# socket may connect to without a public address.
_PROXY_HOSTS = set()


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


_guards_installed = False


def _guarded_create_connection(address, timeout=None, source_address=None, **kwargs):
    """
    yt-dlp's ``create_connection`` (the urllib handler's), with the resolved
    address checked right before each ``connect``: what the socket reaches,
    not what an earlier lookup said.
    """
    import socket

    from yt_dlp.networking import _helper

    if timeout is None:
        timeout = socket._GLOBAL_DEFAULT_TIMEOUT
    create_socket = kwargs.pop('_create_socket_func', _helper._socket_connect)
    host = str(address[0]).strip('[]').lower()
    if host in _PROXY_HOSTS:
        return _helper.create_connection(address, timeout, source_address, _create_socket_func=create_socket, **kwargs)

    def connect_if_public(ip_addr, timeout, source_address):
        if not link_validation.is_public_address(str(ip_addr[4][0])):
            raise BlockedRequest()
        return create_socket(ip_addr, timeout, source_address)

    return _helper.create_connection(address, timeout, source_address, _create_socket_func=connect_if_public, **kwargs)


def install_guards():
    """
    Checks the target of every redirect yt-dlp's handlers follow, and the
    address of every connection of its urllib handler. Idempotent; it only
    refuses non-public addresses, and only yt-dlp's networking uses these
    functions, so it is safe for the whole process.
    """
    global _guards_installed
    if _guards_installed:
        return
    from yt_dlp.networking import _urllib

    _urllib.create_connection = _guarded_create_connection

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
    _guards_installed = True


class GuardedYoutubeDL(yt_dlp.YoutubeDL):
    """A YoutubeDL whose every request and redirect goes to a public address."""

    def __init__(self, params=None, *args, **kwargs):
        from urllib.parse import urlsplit

        install_guards()
        super().__init__(params, *args, **kwargs)
        for key, proxy in (self.proxies or {}).items():
            if key != 'no' and isinstance(proxy, str) and proxy:
                host = urlsplit(proxy if '://' in proxy else f'http://{proxy}').hostname
                if host:
                    _PROXY_HOSTS.add(host.lower())
        director = getattr(self, '_request_director', None)
        handlers = getattr(director, 'handlers', None)
        if isinstance(handlers, dict):
            # Keep only the handler whose connections are guarded (no requests, curl_cffi or websockets).
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
            # The canonical URL is what gets extracted, not what the page said.
            target = link_validation.validate_video_link(target, resolve=True).url
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
    sizes = {}

    def cap(progress):
        # Per file (video, audio...), summed: the cap is for all the formats together.
        name = progress.get('filename') or progress.get('tmpfilename') or ''
        done = progress.get('downloaded_bytes') or 0
        expected = progress.get('total_bytes') or progress.get('total_bytes_estimate') or 0
        sizes[name] = max(sizes.get(name, 0), done, expected)
        if sum(sizes.values()) > max_filesize:
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
