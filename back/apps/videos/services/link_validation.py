"""
Which video links the app accepts, before anything is fetched.

A project by link is downloaded by yt-dlp in the worker. Its generic
extractor fetches any URL, so an unchecked link is a server-side request
forgery: ``http://169.254.169.254/...``, ``http://localhost:8000/admin/`` or a
host of the internal network. Every link goes through ``validate_video_link``
first: when a project is created (VideoProjectSerializer), when probe-link
reads it, and again right before the download (``download_from_youtube``),
for rows saved before this check existed.

The rules:

- only ``http`` and ``https``; no user or password in the URL; only the
  default ports;
- the host is one of the sites of ``LINK_PROVIDERS`` (or a subdomain of the
  ones marked so), never an IP address;
- with ``resolve=True`` every address the host resolves to must be public
  (no loopback, private, link-local, multicast or reserved range).

The messages are Spanish, for the user; the link itself never reaches a log.
"""
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

MAX_LINK_LENGTH = 500

# (domain, provider shown to the user, subdomains allowed). The same sites as
# the web app's PROVIDERS (front/src/features/proyectos/project-view.ts).
LINK_PROVIDERS = (
    ('youtube.com', 'YouTube', True),
    ('youtu.be', 'YouTube', False),
    ('vimeo.com', 'Vimeo', True),
    ('twitch.tv', 'Twitch', True),
    ('drive.google.com', 'Google Drive', False),
)

INVALID_MESSAGE = "Ingresá una URL válida, empezando por https://"
UNSUPPORTED_MESSAGE = "No reconocemos este sitio. Probá con YouTube, Vimeo u otro sitio público."


class LinkRejected(ValueError):
    """
    The link is not one the app fetches. ``code`` is ``invalid`` (not a web
    URL) or ``unsupported`` (a site, address or port it does not fetch).
    """

    def __init__(self, code, message):
        super().__init__(code)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedLink:
    # Normalized: https, lowercase host, no fragment.
    url: str
    host: str
    provider: str


def provider_for_host(host):
    """The provider of an allowed host, or None."""
    for domain, provider, subdomains in LINK_PROVIDERS:
        if host == domain or (subdomains and host.endswith('.' + domain)):
            return provider
    return None


def _is_ip_literal(host):
    try:
        ipaddress.ip_address(host.strip('[]'))
    except ValueError:
        return False
    return True


def _is_public(address):
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_public_host(host, port=443):
    """
    Raises LinkRejected when the host resolves to an address that is not
    public. A host that does not resolve raises the OSError of getaddrinfo
    (it may be transient: the caller decides).
    """
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = {info[4][0] for info in infos}
    if not addresses or not all(_is_public(address) for address in addresses):
        raise LinkRejected('unsupported', UNSUPPORTED_MESSAGE)


def validate_video_link(raw, *, resolve=False):
    """
    The normalized link, or LinkRejected. ``resolve`` also checks the
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
    if _is_ip_literal(host):
        raise LinkRejected('unsupported', UNSUPPORTED_MESSAGE)
    try:
        host = host.encode('idna').decode('ascii').lower()
    except UnicodeError:
        raise LinkRejected('invalid', INVALID_MESSAGE)
    provider = provider_for_host(host)
    if provider is None:
        raise LinkRejected('unsupported', UNSUPPORTED_MESSAGE)
    if resolve:
        check_public_host(host)
    url = urlunsplit(('https', host, parts.path or '/', parts.query, ''))
    return ValidatedLink(url=url, host=host, provider=provider)
