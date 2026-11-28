"""
Ayrshare (https://www.ayrshare.com/docs): one User Profile per workspace. Its
Profile-Key, a credential, goes in the `Profile-Key` header of every call and
nowhere else (never a log line, never error_log).

What this module relies on, from Ayrshare's documentation (read on
2026-11-27; the pages are under /docs/apis and /docs/errors):

- Base URL https://api.ayrshare.com/api (the setting AYRSHARE_BASE_URL, which a
  rehearsal may point at a local stand-in); `Authorization: Bearer AYRSHARE_API_KEY`.
- POST /post with `post`, `platforms`, `mediaUrls`, `isVideo`, `idempotencyKey`,
  `tikTokOptions` and `youTubeOptions`. The answer can come wrapped in `posts`
  (one item per profile). Success is decided by the body, not by the HTTP
  status alone: `status` and the network's `postIds[].status`, and `errors[]`
  with a numeric `code` (and `retryAvailable` when Ayrshare says it can be
  tried again). TikTok answers `id: "pending"` and no `postUrl` until it
  processes the video.
- `idempotencyKey`: unique per User Profile; a repeated key is refused
  whatever happened to the first post (success, error, pending or deleted).
  The code of that refusal is not documented: _rejection recognises it only
  by its message, and the publisher treats an unknown refusal on a retry as
  an unknown outcome.
- Codes: 106 monthly post quota (HTTP 429), 137 duplicate content within 48 h,
  144 profile not found, 146 profile title taken, 156 network not linked,
  161 and 129 authorization to renew (documented for Facebook and LinkedIn;
  the codes of an expired TikTok, Instagram or YouTube link are not), 531
  TikTok unavailable (503, `retryAvailable`).
- YouTube: `youTubeOptions.title` is required (100 characters at most) and
  `visibility` defaults to private; `shorts: true` asks for a Short.
- Profiles: POST /profiles {title (unique)} answers the profileKey only once;
  POST /profiles/generateJWT {profileKey, allowedSocial, redirect, language}
  answers a linking `url` valid for 5 minutes; GET /user (Profile-Key) lists
  `activeSocialAccounts` and `displayNames` (cached 60 s by Ayrshare);
  DELETE /profiles/social {platform} unlinks (200 even if not linked);
  DELETE /profiles deletes the profile. Without a Profile-Key those calls act
  on the primary profile, so none of them is ever sent without one.
"""
import logging
from datetime import datetime, timezone as dt_timezone
from urllib.parse import urlsplit

import requests
from django.conf import settings
from urllib3 import exceptions as urllib3_exceptions
from urllib3.exceptions import ConnectTimeoutError, MaxRetryError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .base import (
    ACCOUNTS_TIMEOUT_SECONDS,
    ADMIN_TIMEOUT_SECONDS,
    INSTAGRAM_REELS,
    PLATFORMS,
    PUBLISH_TIMEOUT_SECONDS,
    TIKTOK,
    YOUTUBE_SHORTS,
    Account,
    AccountStatus,
    CreatedProfile,
    PublishResult,
    SocialProvider,
)
from .errors import (
    ACCOUNT_EXPIRED,
    ACCOUNT_NOT_CONNECTED,
    DUPLICATE_CONTENT,
    PROFILE_NOT_FOUND,
    PROFILE_TITLE_TAKEN,
    PROVIDER_NOT_CONFIGURED,
    PROVIDER_REJECTED,
    PROVIDER_UNAVAILABLE,
    QUOTA_EXCEEDED,
    TIKTOK_OPTIONS_MISSING,
    OutcomeUnknownError,
    PermanentError,
    RetryableError,
    SocialProviderError,
)

logger = logging.getLogger(__name__)

NETWORK = {TIKTOK: 'tiktok', INSTAGRAM_REELS: 'instagram', YOUTUBE_SHORTS: 'youtube'}
# The contract's privacy levels as tikTokOptions.visibility.
TIKTOK_VISIBILITY = {
    'PUBLIC_TO_EVERYONE': 'public',
    'FOLLOWER_OF_CREATOR': 'followers',
    'MUTUAL_FOLLOW_FRIENDS': 'friends',
    'SELF_ONLY': 'private',
}
YOUTUBE_TITLE_MAX = 100
# Shown on top of Ayrshare's linking page instead of "Social Accounts for <title>".
LINKING_PAGE_HEADER = 'Conectá tus redes a OneCreator'

CODE_QUOTA = 106
CODE_DUPLICATE = 137
CODE_PROFILE_NOT_FOUND = 144
CODE_TITLE_TAKEN = 146
CODE_NOT_LINKED = 156
CODES_RELINK = frozenset({129, 161})


def profile_title(workspace):
    """
    The profile's title at Ayrshare: unique there, and the same for every try,
    so a creation whose answer was lost is refused (146) instead of leaving a
    second, paid profile. The prefix tells environments apart when they share
    an Ayrshare account.
    """
    return f"{settings.SOCIAL_PROFILE_TITLE_PREFIX}-{workspace.pk}"


def _url(path):
    """AYRSHARE_BASE_URL (https in production: backend.settings.prod checks it) and the endpoint's path."""
    return f"{settings.AYRSHARE_BASE_URL.rstrip('/')}{path}"


def _link_schemes():
    """
    The linking page opens in the user's browser: https only. A local stand-in served over http
    (AYRSHARE_BASE_URL http, which only DEBUG and the tests allow) may answer an http page of its own.
    """
    return ('https://',) if urlsplit(settings.AYRSHARE_BASE_URL).scheme == 'https' else ('https://', 'http://')


class AyrshareProvider(SocialProvider):
    name = 'ayrshare'
    display_name = 'Ayrshare'

    # --- Profiles ---

    def create_profile(self, workspace):
        body = {'title': profile_title(workspace), 'topHeader': LINKING_PAGE_HEADER}
        # A read timeout may have created it: unknown, not retryable.
        data = self._call('post', '/profiles', 'create_profile', json=body, unknown_if_maybe_sent=True)
        ref = data.get('profileKey')
        if not isinstance(ref, str) or not ref:
            raise OutcomeUnknownError(detail='ayrshare create_profile answered no profileKey')
        return CreatedProfile(ref=ref, provider_id=_text(data.get('refId')) or '')

    def connect_url(self, profile, platform, return_url=None):
        body = {'profileKey': _key(profile), 'allowedSocial': [NETWORK[platform]], 'language': 'es'}
        if return_url:
            body['redirect'] = return_url
        data = self._call('post', '/profiles/generateJWT', 'connect_url', json=body)
        url = data.get('url')
        if not isinstance(url, str) or not url.startswith(_link_schemes()):
            raise PermanentError(PROVIDER_REJECTED, 'ayrshare generateJWT answered no url')
        return url

    def accounts(self, profile):
        data = self._call('get', '/user', 'accounts', profile_key=_key(profile), timeout=ACCOUNTS_TIMEOUT_SECONDS)
        active = {name for name in _list(data.get('activeSocialAccounts')) if isinstance(name, str)}
        entries = {
            entry.get('platform'): entry for entry in _list(data.get('displayNames')) if isinstance(entry, dict)
        }
        now = timezone.now()
        accounts = []
        for platform in PLATFORMS:
            network = NETWORK[platform]
            if network not in active:
                accounts.append(Account(platform, AccountStatus.DISCONNECTED))
                continue
            entry = entries.get(network) or {}
            status = AccountStatus.EXPIRED if _refresh_due(entry, now) else AccountStatus.CONNECTED
            accounts.append(Account(platform, status, _username(platform, entry), _datetime(entry.get('created'))))
        return accounts

    def disconnect(self, profile, platform):
        self._call('delete', '/profiles/social', 'disconnect', profile_key=_key(profile), json={'platform': NETWORK[platform]})

    def delete_profile(self, profile):
        if not getattr(profile, 'provider_ref', ''):
            return  # nothing at the provider to delete, and no call without a key
        try:
            self._call('delete', '/profiles', 'delete_profile', profile_key=_key(profile), json={})
        except PermanentError as exc:
            if exc.code != PROFILE_NOT_FOUND:  # 144: already gone
                raise

    # --- Publishing ---

    def publish(self, profile, request):
        key = _key(profile)
        network = NETWORK[request.platform]
        payload = {
            'post': request.text,
            'platforms': [network],
            'mediaUrls': [request.media_url],
            # The presigned URL ends in its signature, not in ".mp4".
            'isVideo': True,
            'idempotencyKey': request.idempotency_key,
        }
        if request.platform == TIKTOK:
            payload['tikTokOptions'] = _tiktok_options(request.tiktok)
        elif request.platform == YOUTUBE_SHORTS:
            # Public: the user asked to publish it (Ayrshare's default is private).
            payload['youTubeOptions'] = {'title': _youtube_title(request.title), 'visibility': 'public', 'shorts': True}

        response = self._send('post', '/post', 'publish', key, payload, PUBLISH_TIMEOUT_SECONDS, unknown_if_maybe_sent=True)
        try:
            result = _publish_outcome(response, network)
        except SocialProviderError as exc:
            logger.warning(
                "social.ayrshare.publish_failed platform=%s error=%s code=%s detail=%s",
                request.platform, type(exc).__name__, exc.code, exc.detail,
            )
            raise
        logger.info("social.ayrshare.published platform=%s pending=%s", request.platform, result.pending)
        return result

    # --- HTTP ---

    def _call(self, method, path, op, profile_key=None, json=None, unknown_if_maybe_sent=False, timeout=ADMIN_TIMEOUT_SECONDS):
        """A call other than publishing: its answer's body, or the error by HTTP status and Ayrshare's code."""
        response = self._send(method, path, op, profile_key, json, timeout, unknown_if_maybe_sent)
        body = _json(response) or {}
        codes = _codes([body])
        if 200 <= response.status_code < 300 and body.get('status') != 'error':
            return body
        detail = _detail(response.status_code, codes)
        logger.warning("social.ayrshare.call_failed op=%s detail=%s", op, detail)
        if CODE_PROFILE_NOT_FOUND in codes:
            raise PermanentError(PROFILE_NOT_FOUND, detail)
        if CODE_TITLE_TAKEN in codes:
            raise PermanentError(PROFILE_TITLE_TAKEN, detail)
        if response.status_code == 401:
            raise PermanentError(PROVIDER_NOT_CONFIGURED, detail)
        if response.status_code == 429:
            raise RetryableError(PROVIDER_UNAVAILABLE, detail, retry_after=_retry_after(response))
        if response.status_code >= 500:
            if unknown_if_maybe_sent:
                raise OutcomeUnknownError(detail=detail)
            raise RetryableError(PROVIDER_UNAVAILABLE, detail, maybe_sent=True)
        raise PermanentError(PROVIDER_REJECTED, detail)

    def _send(self, method, path, op, profile_key, json, timeout, unknown_if_maybe_sent=False):
        """
        The response, or: RetryableError when nothing reached Ayrshare (no
        connection, no name, a proxy or TLS handshake that failed). When the
        request may have reached it without an answer (a read timeout, a
        connection cut after sending, a response cut halfway), OutcomeUnknownError
        for the calls that may have done something (``unknown_if_maybe_sent``:
        publishing, creating a profile), RetryableError(maybe_sent) for the rest.
        """
        api_key = getattr(settings, 'AYRSHARE_API_KEY', None)
        if not api_key:
            logger.error("social.ayrshare.not_configured op=%s", op)
            raise PermanentError(PROVIDER_NOT_CONFIGURED, 'AYRSHARE_API_KEY missing')
        headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
        if profile_key is not None:
            headers['Profile-Key'] = profile_key
        try:
            response = requests.request(method, _url(path), json=json, headers=headers, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            detail = f'ayrshare {type(exc).__name__}'
            if _never_sent(exc):
                raise RetryableError(detail=detail) from exc
            # Sent, and then no answer: Ayrshare may have done it, or still be doing it.
            logger.error("social.ayrshare.no_answer op=%s error=%s", op, type(exc).__name__)
            if unknown_if_maybe_sent:
                raise OutcomeUnknownError(detail=detail) from exc
            raise RetryableError(detail=detail, maybe_sent=True) from exc
        logger.info("social.ayrshare.call op=%s http=%s", op, response.status_code)
        return response


# --- Publish answer ---

def _publish_outcome(response, network):
    """PublishResult, or the error of errors.py the answer means. The body decides first."""
    http = response.status_code
    body = _json(response)
    entry = _entry(body)
    line = next(
        (item for item in _list(entry.get('postIds')) if isinstance(item, dict) and item.get('platform') == network),
        None,
    )
    said = entry.get('status') or (body or {}).get('status')
    if line is not None and line.get('status') == 'success':
        return _published(entry, line)
    if line is not None and line.get('status') is None and line.get('id') and said in ('success', 'scheduled'):
        # A line without its own status in an answer that says success: out.
        return _published(entry, line)
    if said in ('success', 'scheduled') and line is None:
        # The request went through without a line for the network: out, with no link.
        return PublishResult(external_id=_text(entry.get('id')), post_url=None, pending=True)

    errors = [
        item for item in _list(entry.get('errors')) + ([] if entry is body else _list((body or {}).get('errors')))
        if isinstance(item, dict) and item.get('platform') in (network, None)
    ]
    for source in (body, entry, line):
        # A request-level refusal is the whole body ({action, status, code, message}).
        if isinstance(source, dict) and 'code' in source and source not in errors:
            errors.append(source)
    codes = _codes(errors)
    detail = _detail(http, codes)
    if said == 'error' or errors or line is not None:
        raise _rejection(http, codes, errors, detail, _retry_after(response))
    # The body says nothing (not JSON, an HTML page of the CDN): the HTTP status decides.
    if http == 504:
        raise OutcomeUnknownError(detail=detail)
    if http == 429:
        # The rate limit refuses the request before it is processed.
        raise RetryableError(detail=detail, retry_after=_retry_after(response))
    if http >= 500:
        # It reached Ayrshare and we do not know what it did: the same key again, until a clear answer.
        raise RetryableError(detail=detail, maybe_sent=True)
    if http == 401:
        raise PermanentError(PROVIDER_NOT_CONFIGURED, detail)
    if 200 <= http < 300:
        # Accepted, but with an answer we cannot read: it may be out.
        raise OutcomeUnknownError(detail=detail)
    raise PermanentError(PROVIDER_REJECTED, detail)


def _rejection(http, codes, errors, detail, retry_after):
    """
    Ayrshare answered with an error. A documented code is a clear answer that
    the post did not go out; so is a 4xx. A 5xx with an error body that no
    code explains is not: it may have published (504: never retried; another
    5xx: retried with the same key, which Ayrshare refuses if it did).
    """
    if CODE_QUOTA in codes:
        return PermanentError(QUOTA_EXCEEDED, detail)
    if CODE_NOT_LINKED in codes or CODE_PROFILE_NOT_FOUND in codes:
        return PermanentError(ACCOUNT_NOT_CONNECTED, detail)
    if CODES_RELINK & set(codes):
        return PermanentError(ACCOUNT_EXPIRED, detail)
    if CODE_DUPLICATE in codes:
        return PermanentError(DUPLICATE_CONTENT, detail)
    if any('idempoten' in str(item.get('message') or '').lower() for item in errors):
        # The key was used before: an earlier attempt of this same post reached Ayrshare.
        return OutcomeUnknownError(detail=f'{detail} idempotency')
    if any(item.get('retryAvailable') is True for item in errors):
        # Ayrshare says nothing went out and it may work later: retried with a new key.
        return RetryableError(PROVIDER_UNAVAILABLE, detail, retry_after=retry_after, key_spent=True)
    if http == 429:
        # The rate limit refuses the request before it is processed: nothing went out.
        return RetryableError(PROVIDER_UNAVAILABLE, detail, retry_after=retry_after)
    if http == 504:
        return OutcomeUnknownError(detail=detail)
    if http >= 500:
        return RetryableError(PROVIDER_UNAVAILABLE, detail, maybe_sent=True)
    if http == 401:
        return PermanentError(PROVIDER_NOT_CONFIGURED, detail)
    return PermanentError(PROVIDER_REJECTED, detail)


def _published(entry, line):
    url = line.get('postUrl')
    post_url = url if isinstance(url, str) and url.startswith('https://') and len(url) <= 500 else None
    network_id = _text(line.get('id'))
    external_id = _text(entry.get('id')) or (network_id if network_id not in ('pending', 'failed') else None)
    return PublishResult(
        external_id=external_id[:255] if external_id else None,
        post_url=post_url,
        pending=network_id == 'pending' or post_url is None,
    )


def _tiktok_options(options):
    if options is None:
        raise PermanentError(TIKTOK_OPTIONS_MISSING)
    return {
        'visibility': TIKTOK_VISIBILITY[options.privacy_level],
        'disableComments': not options.allow_comment,
        'disableDuet': not options.allow_duet,
        'disableStitch': not options.allow_stitch,
        'isBrandOrganic': options.brand_organic,
        'isBrandedContent': options.branded_content,
    }


def _youtube_title(title):
    # YouTube refuses "<" and ">" in a title.
    clean = ' '.join((title or '').replace('<', '').replace('>', '').split())
    return clean[:YOUTUBE_TITLE_MAX] or 'Clip'


# --- Small readers (never trust the shape of an answer) ---

def _never_sent(exc):
    """
    Whether the request certainly never reached Ayrshare: the connection or its
    TLS handshake or proxy failed (requests then wraps the cause in urllib3's
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


def _key(profile):
    """The Profile-Key, or account_not_connected: a call without one would act on the primary profile."""
    ref = getattr(profile, 'provider_ref', '') or ''
    if not ref:
        raise PermanentError(ACCOUNT_NOT_CONNECTED, 'no profile')
    return ref


def _json(response):
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _entry(body):
    """With a Profile-Key the answer may come in `posts`, one item per profile: this request has one."""
    posts = (body or {}).get('posts')
    if isinstance(posts, list) and posts and isinstance(posts[0], dict):
        return posts[0]
    return body or {}


def _list(value):
    return value if isinstance(value, list) else []


def _text(value):
    if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value):
        return str(value)
    return None


def _codes(items):
    codes = set()
    for item in items:
        try:
            codes.add(int(item.get('code')))
        except (TypeError, ValueError):
            continue
    return sorted(codes)


def _detail(http, codes):
    return f"ayrshare http={http}" + (f" code={','.join(str(code) for code in codes)}" if codes else '')


def _retry_after(response):
    value = (response.headers or {}).get('Retry-After')
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def _datetime(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = parse_datetime(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed is not None and timezone.is_naive(parsed):
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


def _refresh_due(entry, now):
    """The network asks to link it again: its `refreshRequired` date passed, or no days remain."""
    days = entry.get('refreshDaysRemaining')
    if isinstance(days, (int, float)) and not isinstance(days, bool) and days <= 0:
        return True
    due = _datetime(entry.get('refreshRequired'))
    return isinstance(due, datetime) and due <= now


def _username(platform, entry):
    if platform == YOUTUBE_SHORTS:
        name = entry.get('handle') or entry.get('channelTitle')
        return name if isinstance(name, str) and name else None
    name = entry.get('username')
    if not isinstance(name, str) or not name:
        return None
    return name if name.startswith('@') else f'@{name}'
