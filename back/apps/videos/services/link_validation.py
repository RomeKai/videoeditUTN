"""
Which video links the app accepts, before anything is fetched.

A project by link is downloaded by yt-dlp in the worker. An unchecked link
is a server-side request forgery: ``http://169.254.169.254/...``,
``http://localhost:8000/admin/`` or a host of the internal network, directly
or through a page of an allowed site that points elsewhere (YouTube's consent
page hands any ``continue`` URL to yt-dlp). Every link goes through
``validate_video_link`` first: when a project is created
(VideoProjectSerializer), when probe-link reads it, again right before the
download (``download_from_youtube``), for rows saved before this check, and
on every hop yt-dlp follows (``ytdlp_safe``).

The rules:

- only ``http`` and ``https``; no user or password in the URL; only the
  default ports;
- the host is exactly one of ``LINK_HOSTS`` (letters, digits, dots and
  hyphens only), never an IP address and never another subdomain;
- the link names one video: a YouTube link becomes ``watch?v=<id>`` (a list
  or a channel is refused), a Twitch link a VOD or a clip (a channel is a
  live stream), a Vimeo link has its numeric id, a Drive link its file id;
- with ``resolve=True`` every address the host resolves to must be public
  (no loopback, private, link-local, multicast or reserved range).

This module is plain Python on purpose: the probe's child process
(``link_probe_worker.py``) imports it without Django. The messages are
Spanish, for the user; the link itself never reaches a log.
"""
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

MAX_LINK_LENGTH = 500

# Every host the app downloads from, and the provider shown to the user. The
# web app's PROVIDERS (front/src/features/proyectos/project-view.ts) has the
# same list.
LINK_HOSTS = {
    'youtube.com': 'YouTube',
    'www.youtube.com': 'YouTube',
    'm.youtube.com': 'YouTube',
    'music.youtube.com': 'YouTube',
    'youtu.be': 'YouTube',
    'vimeo.com': 'Vimeo',
    'www.vimeo.com': 'Vimeo',
    'player.vimeo.com': 'Vimeo',
    'twitch.tv': 'Twitch',
    'www.twitch.tv': 'Twitch',
    'm.twitch.tv': 'Twitch',
    'clips.twitch.tv': 'Twitch',
    'player.twitch.tv': 'Twitch',
    'drive.google.com': 'Google Drive',
}

INVALID_MESSAGE = "Ingresá una URL válida, empezando por https://"
UNSUPPORTED_MESSAGE = "No reconocemos este sitio. Probá con YouTube, Vimeo u otro sitio público."
NOT_A_VIDEO_MESSAGE = "Pegá el link de un solo video, no de una lista, un canal o una transmisión en vivo."

_HOST_CHARS = re.compile(r'[A-Za-z0-9.-]+')
_LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')
_VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{1,64}')
_YOUTUBE_PATH_ID = re.compile(r'/(?:shorts|live|embed|v)/([A-Za-z0-9_-]{1,64})/?')
_TWITCH_VOD = re.compile(r'/videos/(\d{1,20})/?')
_TWITCH_CLIP = re.compile(r'/[A-Za-z0-9_]{1,64}/clip/([A-Za-z0-9_-]{1,128})/?')
_TWITCH_CLIP_HOST = re.compile(r'/([A-Za-z0-9_-]{1,128})/?')
_VIMEO_PATH = re.compile(r'/(?:[A-Za-z0-9_-]+/){0,3}(\d{1,20})(?:/[0-9a-f]{1,32})?/?')
_VIMEO_PLAYER = re.compile(r'/video/(\d{1,20})/?')
_DRIVE_FILE = re.compile(r'/file/d/([A-Za-z0-9_-]{1,128})(?:/[A-Za-z]*)?/?')


class LinkRejected(ValueError):
    """
    The link is not one the app fetches. ``code`` is ``invalid`` (not a web
    URL) or ``unsupported`` (a site, address, port or kind of page it does
    not fetch).
    """

    def __init__(self, code, message):
        super().__init__(code)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedLink:
    # Canonical: https, the provider's host and only what names the video.
    url: str
    host: str
    provider: str


def provider_for_host(host):
    """The provider of an allowed host, or None."""
    return LINK_HOSTS.get((host or '').lower().rstrip('.'))


def _resolve(host, port):
    """The addresses of ``host``. Its own function so tests replace it, not socket.getaddrinfo."""
    return {info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}


_NAT64 = ipaddress.ip_network('64:ff9b::/96')
_IPV4_COMPATIBLE = ipaddress.ip_network('::/96')


def _embedded_ipv4(ip):
    """The IPv4 addresses an IPv6 address carries: mapped, NAT64, compatible, 6to4, Teredo."""
    embedded = []
    if ip.ipv4_mapped is not None:
        embedded.append(ip.ipv4_mapped)
    if ip in _NAT64 or (ip in _IPV4_COMPATIBLE and int(ip) > 1):
        embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    if ip.sixtofour is not None:
        embedded.append(ip.sixtofour)
    if ip.teredo is not None:
        embedded.extend(ip.teredo)
    return embedded


def is_public_address(address):
    ip = ipaddress.ip_address(str(address).split('%')[0])
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = _embedded_ipv4(ip)
        if embedded:
            # Judged by the IPv4 inside: 64:ff9b::a00:5 is 10.0.0.5.
            return all(is_public_address(str(inner)) for inner in embedded)
    return ip.is_global and not ip.is_multicast


def check_public_host(host, port=443):
    """
    Raises LinkRejected when the host is, or resolves to, an address that is
    not public. A host that does not resolve raises the OSError of the
    lookup (it may be transient: the caller decides).
    """
    try:
        literal = ipaddress.ip_address(host.strip('[]'))
    except ValueError:
        addresses = _resolve(host, port)
    else:
        addresses = {str(literal)}
    if not addresses or not all(is_public_address(address) for address in addresses):
        raise LinkRejected('unsupported', UNSUPPORTED_MESSAGE)


def _not_a_video():
    return LinkRejected('unsupported', NOT_A_VIDEO_MESSAGE)


def _canonical(host, provider, path, query):
    """The URL of the one video the link names, or LinkRejected."""
    path = path or '/'
    params = parse_qs(query, keep_blank_values=False)
    if provider == 'YouTube':
        if host == 'youtu.be':
            video_id = path.strip('/')
        elif path.rstrip('/') == '/watch':
            video_id = (params.get('v') or [''])[0]
        else:
            match = _YOUTUBE_PATH_ID.fullmatch(path)
            video_id = match.group(1) if match else ''
        if not _VIDEO_ID.fullmatch(video_id or ''):
            raise _not_a_video()
        # Only the video: a list, a start time or a tracking parameter goes.
        return f"https://www.youtube.com/watch?{urlencode({'v': video_id})}"
    if provider == 'Twitch':
        if host == 'clips.twitch.tv':
            match = _TWITCH_CLIP_HOST.fullmatch(path)
            if match and match.group(1) != 'embed':
                return f"https://clips.twitch.tv/{match.group(1)}"
            slug = (params.get('clip') or [''])[0]
            if re.fullmatch(r'[A-Za-z0-9_-]{1,128}', slug):
                return f"https://clips.twitch.tv/{slug}"
            raise _not_a_video()
        if host == 'player.twitch.tv':
            video = (params.get('video') or [''])[0].lstrip('v')
            if re.fullmatch(r'\d{1,20}', video):
                return f"https://www.twitch.tv/videos/{video}"
            raise _not_a_video()
        match = _TWITCH_VOD.fullmatch(path)
        if match:
            return f"https://www.twitch.tv/videos/{match.group(1)}"
        match = _TWITCH_CLIP.fullmatch(path)
        if match:
            return f"https://clips.twitch.tv/{match.group(1)}"
        # A channel page is its live stream.
        raise _not_a_video()
    if provider == 'Vimeo':
        if host == 'player.vimeo.com':
            match = _VIMEO_PLAYER.fullmatch(path)
            if not match:
                raise _not_a_video()
            # ``h`` is the key of an unlisted video.
            keep = {key: params[key][0] for key in ('h',) if key in params}
            suffix = f"?{urlencode(keep)}" if keep else ''
            return f"https://player.vimeo.com/video/{match.group(1)}{suffix}"
        if not _VIMEO_PATH.fullmatch(path):
            raise _not_a_video()
        return f"https://vimeo.com{path.rstrip('/')}"
    # Google Drive: one file.
    match = _DRIVE_FILE.fullmatch(path)
    file_id = match.group(1) if match else (params.get('id') or [''])[0]
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', file_id or '') or (not match and path.rstrip('/') not in ('/open', '/uc')):
        raise _not_a_video()
    return f"https://drive.google.com/file/d/{file_id}/view"


def validate_video_link(raw, *, resolve=False):
    """
    The canonical link, or LinkRejected. ``resolve`` also checks the
    addresses of the host (a DNS query): the probe and the download do it,
    the creation does not (it fetches nothing).
    """
    if not isinstance(raw, str):
        raise LinkRejected('invalid', INVALID_MESSAGE)
    value = raw.strip()
    if not value or len(value) > MAX_LINK_LENGTH or any(ch.isspace() or ord(ch) < 32 for ch in value):
        raise LinkRejected('invalid', INVALID_MESSAGE)
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise LinkRejected('invalid', INVALID_MESSAGE)
    if parts.scheme.lower() not in ('http', 'https') or not parts.netloc:
        raise LinkRejected('invalid', INVALID_MESSAGE)
    if '@' in parts.netloc or '\\' in value:
        # user:password@host, or a backslash some parsers read as a slash.
        raise LinkRejected('unsupported', UNSUPPORTED_MESSAGE)
    if port not in (None, 80, 443):
        raise LinkRejected('unsupported', UNSUPPORTED_MESSAGE)
    host = (parts.hostname or '').rstrip('.')
    if not host:
        raise LinkRejected('invalid', INVALID_MESSAGE)
    # Plain ASCII labels only: no "evil.com;.youtube.com", "*.youtube.com",
    # "127.0.0.1%23.youtube.com", IPv6 literals or look-alike characters.
    if not _HOST_CHARS.fullmatch(host) or not all(_LABEL.fullmatch(label) for label in host.lower().split('.')):
        raise LinkRejected('unsupported', UNSUPPORTED_MESSAGE)
    host = host.lower()
    provider = LINK_HOSTS.get(host)
    if provider is None:
        raise LinkRejected('unsupported', UNSUPPORTED_MESSAGE)
    url = _canonical(host, provider, parts.path, parts.query)
    canonical_host = urlsplit(url).hostname
    if resolve:
        check_public_host(canonical_host)
    return ValidatedLink(url=url, host=canonical_host, provider=provider)
