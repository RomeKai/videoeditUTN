"""
Child process of probe-link (``link_probe.probe_link``): reads the metadata
of one video with yt-dlp, without downloading it, and prints one JSON line.

It runs as a script (``python -I link_probe_worker.py <url>``), apart from
the web process, so the parent can kill it when it takes too long. It imports
nothing of the project: only yt-dlp and the standard library.

The output never carries the provider's text: an error is reduced here to a
category (``unavailable``, ``unsupported`` or ``provider_error``) and the
class name of the exception.
"""
import json
import sys

# Same request identity as download_from_youtube (tasks/shared.py), so the
# probe sees what the download will see.
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
)
REFERER = 'https://www.google.com/'
# Every extractor but the generic one: it fetches any URL (SSRF).
ALLOWED_EXTRACTORS = ['default', '-generic']
SOCKET_TIMEOUT_SECONDS = 10

_UNAVAILABLE_MARKERS = (
    'video unavailable', 'private video', 'sign in', 'members-only', 'age-restricted',
    'copyright', 'not available', 'does not exist', 'has been removed', 'removed by',
    'this video is private', 'requires payment', 'premieres in', 'live event will begin',
)
_UNSUPPORTED_MARKERS = ('unsupported url', 'no suitable extractor')


def categorize(message):
    """The category of a yt-dlp error message (lowercased match, never returned)."""
    text = (message or '').lower()
    if any(marker in text for marker in _UNSUPPORTED_MARKERS):
        return 'unsupported'
    if any(marker in text for marker in _UNAVAILABLE_MARKERS):
        return 'unavailable'
    return 'provider_error'


def ydl_options():
    return {
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
        'skip_download': True,
        'noplaylist': True,
        'socket_timeout': SOCKET_TIMEOUT_SECONDS,
        'retries': 0,
        'extractor_retries': 0,
        'allowed_extractors': ALLOWED_EXTRACTORS,
        'user_agent': USER_AGENT,
        'referer': REFERER,
        'logger': _SilentLogger(),
    }


class _SilentLogger:
    """yt-dlp writes errors to stderr: the provider's text stays in this process."""

    def debug(self, msg):
        pass

    info = warning = error = debug


# A link can resolve to another one (``watch?v=..&list=..`` gives the video's
# own URL). It is followed only through these extractors, forced by key: each
# one reads only its own site's URLs.
FOLLOW_EXTRACTORS = frozenset({'Youtube', 'Vimeo', 'TwitchVod', 'TwitchClips', 'GoogleDrive'})
MAX_HOPS = 2


def _follow(ydl, info):
    for _hop in range(MAX_HOPS):
        if not isinstance(info, dict) or info.get('_type') not in ('url', 'url_transparent'):
            return info
        ie_key, target = info.get('ie_key'), info.get('url')
        if ie_key not in FOLLOW_EXTRACTORS or not isinstance(target, str):
            return info
        inner = ydl.extract_info(target, download=False, process=False, ie_key=ie_key)
        if isinstance(inner, dict) and info.get('_type') == 'url_transparent':
            # The outer result's fields win, as yt-dlp does when it processes it.
            outer = {k: v for k, v in info.items() if k not in ('_type', 'url', 'ie_key') and v is not None}
            inner = {**inner, **outer}
        info = inner
    return info


def summarize(info):
    """The fields probe-link answers with, from a yt-dlp info dict."""
    kind = info.get('_type') or 'video'
    return {
        'kind': kind,
        'title': info.get('title') if isinstance(info.get('title'), str) else '',
        'duration': info.get('duration') if isinstance(info.get('duration'), (int, float)) else None,
        'is_live': bool(info.get('is_live')),
        'thumbnail': info.get('thumbnail') if isinstance(info.get('thumbnail'), str) else None,
        'webpage_url': info.get('webpage_url') if isinstance(info.get('webpage_url'), str) else None,
    }


def probe(url):
    try:
        import yt_dlp
    except ImportError:
        return {'error': 'tool_missing', 'class': 'ImportError'}
    try:
        with yt_dlp.YoutubeDL(ydl_options()) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
            info = _follow(ydl, info)
    except Exception as exc:
        return {'error': categorize(str(exc)), 'class': type(exc).__name__}
    if not isinstance(info, dict):
        return {'error': 'provider_error', 'class': type(info).__name__}
    return {'ok': summarize(info)}


def main(argv):
    if len(argv) != 2:
        print(json.dumps({'error': 'provider_error', 'class': 'UsageError'}))
        return 2
    result = probe(argv[1])
    print(json.dumps(result))
    return 0 if 'ok' in result else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
