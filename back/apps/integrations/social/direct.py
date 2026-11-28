"""Direct publication and the grant contract implemented by DIST-01."""
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.utils.module_loading import import_string

from .base import INSTAGRAM_REELS, TIKTOK, YOUTUBE_SHORTS
from .errors import (
    ACCOUNT_EXPIRED, ACCOUNT_NOT_CONNECTED, PROVIDER_NOT_CONFIGURED, OutcomeUnknownError, PermanentError,
    RetryableError,
)

DIRECT = 'direct'
INTERMEDIARY = 'intermediary'
BACKOFF = (1, 5, 30)
UPLOAD_BUDGET_SECONDS = 280


@dataclass(frozen=True)
class PublishingGrant:
    access_token: str = field(repr=False)
    account_id: str = ''
    # The direct TikTok Export flow must capture consent for this scheduled post
    # after displaying the current creator information and privacy choices.
    tiktok_consent: bool = False


def account_source():
    path = getattr(settings, 'SOCIAL_DIRECT_ACCOUNT_SOURCE', '')
    return import_string(path) if path else None


def direct_account_connected(workspace_id, platform):
    source = account_source()
    # The account source is an object with a local is_connected lookup and a
    # worker-only get_grant lookup, which can refresh OAuth credentials.
    return source is not None and source.is_connected(workspace_id, platform)


def get_grant(workspace_id, platform, post_id):
    source = account_source()
    grant = source.get_grant(workspace_id, platform, post_id) if source is not None else None
    if grant is None or not grant.access_token:
        raise PermanentError(ACCOUNT_NOT_CONNECTED)
    return grant


def same_ayrshare_account(workspace_id, platform):
    source = account_source()
    confirm = getattr(source, 'same_ayrshare_account', None)
    return confirm is not None and confirm(workspace_id, platform)


def checked_upload_url(url, allowed_hosts):
    parsed = urlsplit(url or '')
    if (
        parsed.scheme != 'https' or parsed.hostname not in allowed_hosts or parsed.netloc != parsed.hostname
        or parsed.fragment
    ):
        raise PermanentError(PROVIDER_NOT_CONFIGURED, 'unrecognized direct upload URL')
    return url


def safe_id(value):
    if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:~-]{1,200}', value):
        return value
    return None


def response_json(response, *, uncertain=False):
    try:
        data = response.json()
        if isinstance(data, dict):
            return data
    except ValueError:
        pass
    error = OutcomeUnknownError if uncertain else PermanentError
    raise error(detail='invalid direct API response')


class DirectHTTP:
    """Bounded HTTP calls. Only replay requests known not to have published."""

    def __init__(self, session, deadline):
        self.session = session
        self.deadline = deadline

    def request(self, method, url, *, accepted=(200,), uncertain=False, **kwargs):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            if uncertain:
                raise OutcomeUnknownError(detail='direct publishing deadline')
            raise RetryableError(detail='direct publishing deadline')
        try:
            response = self.session.request(
                method, url, timeout=(min(5, remaining), min(30, remaining)), allow_redirects=False, **kwargs,
            )
        except requests.ConnectTimeout:
            raise RetryableError(detail='direct provider connect timeout') from None
        except requests.RequestException:
            error = OutcomeUnknownError if uncertain else RetryableError
            raise error(detail='direct provider connection failed') from None
        code = response.status_code
        if code in accepted:
            return response
        if code == 401:
            raise PermanentError(ACCOUNT_EXPIRED, 'HTTP 401')
        if code == 429:
            delay = response.headers.get('Retry-After', '')[:6]
            seconds = int(delay) if delay.isascii() and delay.isdecimal() else 1
            raise RetryableError(detail='HTTP 429', retry_after=max(1, min(seconds, 240)))
        if uncertain and code in (408, 409, 416):
            raise OutcomeUnknownError(detail=f'HTTP {code}')
        if code >= 500:
            error = OutcomeUnknownError if uncertain else RetryableError
            raise error(detail=f'HTTP {code}')
        raise PermanentError(detail=f'HTTP {code}')

    def poll(self, check, *, interval, uncertain=True):
        while self.deadline > time.monotonic():
            try:
                result = check()
            except RetryableError:
                result = None
            if result is not None:
                return result
            if self.deadline - time.monotonic() <= interval:
                break
            time.sleep(interval)
        error = OutcomeUnknownError if uncertain else RetryableError
        raise error(detail='direct provider processing deadline')


def publisher_for(platform):
    from apps.integrations.youtube_api import YouTubePublisher

    from .direct_platforms import InstagramPublisher, TikTokPublisher

    return {
        YOUTUBE_SHORTS: YouTubePublisher,
        TIKTOK: TikTokPublisher,
        INSTAGRAM_REELS: InstagramPublisher,
    }[platform]
