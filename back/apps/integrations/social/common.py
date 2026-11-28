"""
What the providers share: whether a failed request ever reached the provider,
readers that never trust the shape of an answer, and YouTube's title rule.
Nothing here talks to a provider or knows its names.
"""
from urllib.parse import urlsplit

import requests
from urllib3 import exceptions as urllib3_exceptions
from urllib3.exceptions import ConnectTimeoutError, MaxRetryError

YOUTUBE_TITLE_MAX = 100


def never_sent(exc):
    """
    Whether the request certainly never reached the provider: the connection or
    its TLS handshake or proxy failed (requests then wraps the cause in urllib3's
    MaxRetryError), the connect timed out, or the URL was refused before
    sending. A connection reset or closed after the request was written
    (ProtocolError, RemoteDisconnected), a read timeout or a response cut
    halfway may have been processed.
    """
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return True
    if isinstance(exc, requests.exceptions.ConnectionError):
        reason = exc.args[0] if exc.args else None
        return isinstance(reason, MaxRetryError) and isinstance(
            reason.reason, (ConnectTimeoutError, urllib3_exceptions.SSLError, urllib3_exceptions.ProxyError),
        )
    return isinstance(exc, (
        requests.exceptions.InvalidURL, requests.exceptions.MissingSchema,
        requests.exceptions.InvalidSchema, requests.exceptions.InvalidHeader,
    ))


def link_schemes(base_url):
    """
    The schemes a linking page may have: it opens in the user's browser, so https only. A local
    stand-in served over http (a base URL with http, which only DEBUG and the tests allow) may
    answer an http page of its own.
    """
    return ('https://',) if urlsplit(base_url).scheme == 'https' else ('https://', 'http://')


def json_object(response):
    """The answer's body when it is a JSON object, else None."""
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def as_list(value):
    return value if isinstance(value, list) else []


def text_value(value):
    if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
        return str(value)
    return None


def youtube_title(title):
    """YouTube refuses "<" and ">" in a title, and more than 100 characters; never empty."""
    clean = ' '.join((title or '').replace('<', '').replace('>', '').split())
    return clean[:YOUTUBE_TITLE_MAX] or 'Clip'
