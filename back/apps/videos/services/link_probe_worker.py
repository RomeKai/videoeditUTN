"""
Child process of probe-link (``link_probe.probe_link``): reads the metadata
of one video with yt-dlp, without downloading it, and prints one JSON line.

It runs as a script (``python -I link_probe_worker.py <url>``), apart from
the web process, in its own process group and with a minimal environment
(no secrets of the web process), so the parent can kill it and everything it
started when it takes too long. It imports only yt-dlp, the standard library
and the two plain modules next to it: ``ytdlp_safe`` (how the app runs
yt-dlp) and ``link_validation``.

The output never carries the provider's text: an error is reduced here to a
category (``unavailable``, ``unsupported`` or ``provider_error``) and the
class name of the exception.
"""
import json
import os
import sys

_UNAVAILABLE_MARKERS = (
    'video unavailable', 'private video', 'sign in', 'members-only', 'age-restricted',
    'copyright', 'not available', 'does not exist', 'has been removed', 'removed by',
    'this video is private', 'requires payment', 'premieres in', 'live event will begin',
)
_UNSUPPORTED_MARKERS = ('unsupported url', 'no suitable extractor', 'blocked non-public address')


def categorize(message):
    """The category of a yt-dlp error message (lowercased match, never returned)."""
    text = (message or '').lower()
    if any(marker in text for marker in _UNSUPPORTED_MARKERS):
        return 'unsupported'
    if any(marker in text for marker in _UNAVAILABLE_MARKERS):
        return 'unavailable'
    return 'provider_error'


def _safe():
    """ytdlp_safe, imported the same way as a script or inside the package."""
    try:
        from . import ytdlp_safe
    except ImportError:
        # Appended, not prepended: nothing next to this file can shadow the standard library.
        here = os.path.dirname(os.path.abspath(__file__))
        if here not in sys.path:
            sys.path.append(here)
        import ytdlp_safe
    return ytdlp_safe


def ydl_options():
    options = _safe().base_options()
    options.update({'skip_download': True, 'retries': 0, 'extractor_retries': 0})
    return options


def summarize(info):
    """The fields probe-link answers with, from a yt-dlp info dict."""
    return {
        'kind': info.get('_type') or 'video',
        'title': info.get('title') if isinstance(info.get('title'), str) else '',
        'duration': info.get('duration') if isinstance(info.get('duration'), (int, float)) else None,
        'is_live': bool(info.get('is_live')),
        'thumbnail': info.get('thumbnail') if isinstance(info.get('thumbnail'), str) else None,
        'webpage_url': info.get('webpage_url') if isinstance(info.get('webpage_url'), str) else None,
    }


def probe(url):
    try:
        safe = _safe()
    except ImportError:
        return {'error': 'tool_missing', 'class': 'ImportError'}
    try:
        with safe.GuardedYoutubeDL(ydl_options()) as ydl:
            info = safe.extract_single_video(ydl, url)
    except safe.LinkRefused as exc:
        if exc.code == 'unsupported':
            return {'error': 'unsupported', 'class': 'LinkRefused'}
        return {'ok': {'kind': 'live' if exc.code == 'live' else 'playlist', 'title': '', 'duration': None,
                       'is_live': exc.code == 'live', 'thumbnail': None, 'webpage_url': None}}
    except Exception as exc:
        return {'error': categorize(str(exc)), 'class': type(exc).__name__}
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
