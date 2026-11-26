"""
probe-link (C1 › Pegar link): what a video link is before a project is
created from it: its site, title and duration, without downloading it.

- The link goes through ``validate_video_link(resolve=True)`` first: only the
  sites of LINK_PROVIDERS, never an IP or a host that resolves to a private
  address, so nothing is fetched for a link the app would not download.
- yt-dlp (the tool the ingestion downloads links with, through ``ytdlp_safe``)
  runs in a child process (``link_probe_worker.py``) with
  LINK_PROBE_TIMEOUT_SECONDS of wall time. The child starts its own process
  group, so when it runs out the whole group is killed, anything it started
  included (yt-dlp may run a JavaScript runtime). It gets a minimal
  environment (``child_environment``): never the web process's secrets.
  Inside, each socket waits at most ``SOCKET_TIMEOUT_SECONDS`` and nothing is
  retried.
- The provider's text never leaves the child: it answers with a category and
  a class name. Logs carry ids, the provider name and those codes only.
- A link read correctly is cached LINK_PROBE_CACHE_SECONDS (keyed by a hash of
  the normalized URL), so the debounced field of C1 does not ask twice.
"""
import hashlib
import json
import logging
import os
import signal
import subprocess
import sys
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache

from .link_validation import (
    NOT_A_VIDEO_MESSAGE, UNSUPPORTED_MESSAGE, LinkRejected, provider_for_host, validate_video_link,
)

logger = logging.getLogger(__name__)

WORKER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'link_probe_worker.py')
CACHE_PREFIX = 'link-probe:v1:'
MAX_TITLE_CHARS = 255
MAX_THUMBNAIL_CHARS = 1000


def _timeout_seconds():
    return getattr(settings, 'LINK_PROBE_TIMEOUT_SECONDS', 25)


def _cache_seconds():
    return getattr(settings, 'LINK_PROBE_CACHE_SECONDS', 600)


class LinkProbeError(Exception):
    """The link cannot be read. ``code`` is the contract's code, ``status`` the HTTP status."""

    def __init__(self, code, status, message):
        super().__init__(code)
        self.code = code
        self.status = status
        self.message = message


UNAVAILABLE = (
    'unavailable', 400,
    "No pudimos acceder a este video. Puede ser privado, haberse borrado o pedir iniciar sesión.",
)
NOT_A_VIDEO = ('unsupported', 400, NOT_A_VIDEO_MESSAGE)
TIMEOUT = ('timeout', 504, "El sitio del video tardó demasiado en responder. Probá de nuevo en unos minutos.")
PROVIDER_ERROR = ('provider_error', 502, "No pudimos consultar el sitio del video. Probá de nuevo en unos minutos.")
TOOL_MISSING = ('unavailable_tool', 503, "No podemos revisar links en este momento. Probá de nuevo más tarde.")


def _error(spec):
    return LinkProbeError(*spec)


@dataclass(frozen=True)
class LinkInfo:
    video_url: str
    provider: str
    title: str
    duration_seconds: float
    thumbnail_url: object  # str or None

    def as_dict(self):
        return {
            'video_url': self.video_url,
            'provider': self.provider,
            'title': self.title,
            'duration_seconds': self.duration_seconds,
            'thumbnail_url': self.thumbnail_url,
        }


# What the child may see of the environment: where programs are, the locale,
# and the outbound proxy with its CA bundle. Nothing else: the web process
# holds SECRET_KEY, the database URL, the R2 and provider keys.
CHILD_ENV_KEYS = (
    'PATH', 'HOME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'TMPDIR',
    'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'no_proxy', 'all_proxy',
    'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
)


def child_environment():
    return {key: os.environ[key] for key in CHILD_ENV_KEYS if key in os.environ}


def _kill_group(process):
    """Kills the child's process group (the child and whatever it started)."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass  # already gone


def run_worker(url):
    """
    Runs yt-dlp in the child process and returns its parsed answer. Raises
    LinkProbeError(TIMEOUT) when it takes longer than the limit (the child's
    whole process group is killed) and TOOL_MISSING when the interpreter
    cannot start it.
    """
    try:
        process = subprocess.Popen(
            [sys.executable, '-I', WORKER_PATH, url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            # The provider's messages stay out of the logs and the response.
            stderr=subprocess.DEVNULL,
            env=child_environment(),
            start_new_session=True,
        )
    except OSError:
        raise _error(TOOL_MISSING)
    try:
        stdout, _ = process.communicate(timeout=_timeout_seconds())
    except subprocess.TimeoutExpired:
        # Not reaped yet, so the group id cannot belong to anyone else. A
        # grandchild that still holds the output pipe also ends up here.
        _kill_group(process)
        process.communicate()
        raise _error(TIMEOUT)
    try:
        lines = stdout.decode('utf-8', errors='replace').strip().splitlines()
        answer = json.loads(lines[-1]) if lines else {}
    except ValueError:
        return {}
    return answer if isinstance(answer, dict) else {}


def _class_name(answer):
    """The exception class the child reported, if it looks like one (for the log)."""
    name = answer.get('class')
    return name if isinstance(name, str) and name.isidentifier() and len(name) <= 64 else None


def _https_url(value, max_chars):
    if isinstance(value, str) and value.startswith('https://') and len(value) <= max_chars:
        return value
    return None


def _from_worker(link, answer):
    if 'ok' not in answer:
        category = answer.get('error')
        if category == 'unavailable':
            raise _error(UNAVAILABLE)
        if category == 'unsupported':
            raise LinkProbeError('unsupported', 400, UNSUPPORTED_MESSAGE)
        if category == 'tool_missing':
            raise _error(TOOL_MISSING)
        raise _error(PROVIDER_ERROR)
    info = answer['ok']
    if not isinstance(info, dict):
        raise _error(PROVIDER_ERROR)
    if info.get('kind') not in ('video', None) or info.get('is_live'):
        raise _error(NOT_A_VIDEO)
    duration = info.get('duration')
    if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0:
        # No duration: a live stream, a premiere or a page that is not a video.
        raise _error(NOT_A_VIDEO)
    # The canonical page of the video when it is on the same site (it drops
    # the list and the tracking parameters), else the link as it came.
    video_url = link.url
    canonical = info.get('webpage_url')
    if isinstance(canonical, str):
        try:
            canonical_link = validate_video_link(canonical)
        except LinkRejected:
            canonical_link = None
        if canonical_link is not None and canonical_link.provider == link.provider:
            video_url = canonical_link.url
    title = (info.get('title') or '').strip()[:MAX_TITLE_CHARS]
    return LinkInfo(
        video_url=video_url,
        provider=link.provider,
        title=title or f"Video de {link.provider}",
        duration_seconds=round(float(duration), 3),
        thumbnail_url=_https_url(info.get('thumbnail'), MAX_THUMBNAIL_CHARS),
    )


def _cache_key(url):
    return CACHE_PREFIX + hashlib.sha256(url.encode('utf-8')).hexdigest()


def probe_link(raw_url, *, log_context=None):
    """
    LinkInfo of a link, or LinkProbeError / LinkRejected. ``log_context``
    (ids only) goes into the log lines.
    """
    context = log_context or {}
    try:
        link = validate_video_link(raw_url, resolve=True)
    except LinkRejected as exc:
        logger.info("probe_link.rejected code=%s user_id=%s", exc.code, context.get('user_id'))
        raise
    except OSError as exc:
        # The host does not resolve: the site cannot be reached now.
        logger.warning(
            "probe_link.dns_failed provider=%s user_id=%s error=%s",
            provider_for_host(_host_of(raw_url)), context.get('user_id'), type(exc).__name__,
        )
        raise _error(PROVIDER_ERROR)

    key = _cache_key(link.url)
    try:
        cached = cache.get(key)
    except Exception:  # a cache outage must not block the probe
        cached = None
    if isinstance(cached, dict):
        return LinkInfo(**cached)

    answer = {}
    try:
        answer = run_worker(link.url)
        info = _from_worker(link, answer)
    except LinkProbeError as exc:
        logger.info(
            "probe_link.failed provider=%s code=%s error=%s user_id=%s",
            link.provider, exc.code, _class_name(answer), context.get('user_id'),
        )
        raise
    try:
        cache.set(key, info.as_dict(), _cache_seconds())
    except Exception:
        pass
    logger.info("probe_link.ok provider=%s user_id=%s", link.provider, context.get('user_id'))
    return info


def _host_of(raw_url):
    from urllib.parse import urlsplit

    try:
        return (urlsplit(str(raw_url).strip()).hostname or '').lower()
    except ValueError:
        return ''
