"""
Upload-Post (https://docs.upload-post.com): one profile per workspace, named
by a username derived from the workspace id (profile_username). That username
is SocialProfile.provider_ref and the `user` of every call; it is not a
secret. Every call carries `Authorization: Apikey UPLOAD_POST_API_KEY`, which
never goes to a log line or to error_log.

What this module relies on, from Upload-Post's documentation (read on
2026-11-28: /api/user-profiles, /api/upload-video, /api/upload-status,
/api/get-tiktok-settings, /api/error-codes, /guides/error-handling and
/guides/rate-limits):

- Base URL https://api.upload-post.com (the setting UPLOAD_POST_BASE_URL, which
  a rehearsal may point at a local stand-in); every path starts with /api.
- Profiles: POST /uploadposts/users {username} answers 201, or 409 when that
  username exists (an earlier creation whose answer was lost): the same
  profile, so creating is idempotent. GET /uploadposts/users/{username} lists
  `profile.social_accounts.{tiktok|instagram|youtube}`: an object with
  `handle`, `display_name` and `reauth_required` when linked; null or "" when
  not. DELETE /uploadposts/users {username}; 404 means it is gone.
  DELETE /uploadposts/users/social {profile_username, social_platform}: 200
  even when it was not linked, 404 when it never was.
- Linking: POST /uploadposts/users/generate-jwt {username, platforms,
  redirect_url, language, ...} answers `access_url`, valid for 48 hours.
- TikTok's creator info: GET /uploadposts/tiktok/settings?profile= answers
  `privacy_level_options` (the levels this account may publish with) and
  `comment_disabled`, `duet_disabled`, `stitch_disabled`.
- Publishing: POST /upload, multipart: `user`, `platform[]`, `video` (a public
  URL), `title` (required for YouTube), per-network `<network>_title`,
  `youtube_description`, TikTok's `privacy_level` (the same four values as our
  contract), `disable_comment`/`disable_duet`/`disable_stitch`,
  `brand_content_toggle`/`brand_organic_toggle`, and our own `request_id`.
  The `Idempotency-Key` header: for 24 hours, the same key answers the
  original job instead of a new one (`idempotent_replay`), and while that job
  is still being made, 409 `IDEMPOTENCY_KEY_IN_USE`.
- The answer: HTTP 200 with `results.<network>` = {success, url | error,
  error_code}; a network the profile has no account for is `skipped` with
  `skip_reason: profile_platform_not_configured` (a request where none is
  connected is a 400 with `invalid_platforms`). An upload that takes more than
  59 s goes on in the background and answers only `request_id`; so does any
  upload Upload-Post finishes later. GET /uploadposts/status?request_id= then
  has the top-level `status` (pending, queued, processing, in_progress,
  completed, failed, not_found) and one entry per network in `results`, with
  its own `status` (queued, processing, completed, failed, retryable,
  skipped). An entry can say `success: true` while still queued: only its
  status, or a finished request, says it is out. On a 504, the docs say not to
  send it again but to look the request up by its own request_id.
- Errors: {success: false, message, error_code}; 401 for the key; 429 for the
  rate limit (Retry-After, or `retry_after`) and for the monthly allowance
  (`usage`). Per network, `error_code` (account_reauth_required,
  daily_cap_reached, monthly_limit_reached, tiktok_privacy_unavailable,
  upload_interrupted, ...): this module follows the "What to do" column of
  the docs' table (see _platform_error).

Asynchronous uploads (the decision of this module): publish() waits for the
network's result inside the provider layer, by reading the status of its own
request_id every STATUS_POLL_SECONDS, and never longer in all than
PUBLISH_TIMEOUT_SECONDS[1] (so PUBLISH_LOCK_TTL_SECONDS still covers it). A
confirmed result is a PublishResult or the network's error; anything else at
the deadline is OutcomeUnknownError: never PUBLISHED without a result, and
never sent again by the worker. The same status check settles a 504 and a
connection lost after sending, before they are reported as unknown.
"""
import logging
import re
import time
from urllib.parse import quote

import requests
from django.conf import settings

from .base import (
    ACCOUNTS_TIMEOUT_SECONDS,
    ADMIN_TIMEOUT_SECONDS,
    INSTAGRAM_REELS,
    PLATFORMS,
    PUBLISH_TIMEOUT_SECONDS,
    TIKTOK,
    TIKTOK_PRIVACY_LEVELS,
    YOUTUBE_SHORTS,
    Account,
    AccountStatus,
    CreatedProfile,
    PublishResult,
    SocialProvider,
)
from .common import as_list, json_object, link_schemes, never_sent, text_value, youtube_title
from .errors import (
    ACCOUNT_EXPIRED,
    ACCOUNT_NOT_CONNECTED,
    DUPLICATE_CONTENT,
    PROFILE_NOT_FOUND,
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
# Shown on top of Upload-Post's linking page.
LINKING_PAGE_TITLE = 'Conectá tus redes a OneCreator'
LINKING_PAGE_BACK = 'Volver a OneCreator'

# The upload itself: Upload-Post answers within about 59 s (then it goes on in the background).
UPLOAD_TIMEOUT_SECONDS = (PUBLISH_TIMEOUT_SECONDS[0], 120)
# The whole publish, the upload and the status checks after it.
PUBLISH_DEADLINE_SECONDS = PUBLISH_TIMEOUT_SECONDS[1]
# Upload-Post caches a status for 2 to 3 s and asks for one read every 5 to 10 s.
STATUS_POLL_SECONDS = 10
STATUS_TIMEOUT_SECONDS = (3, 10)
# Seconds to wait before asking again for a job still being made (409 IDEMPOTENCY_KEY_IN_USE).
KEY_IN_USE_WAIT_SECONDS = 10

# Upload-Post's usernames: letters, numbers, "_", "@" and "-".
_USERNAME_UNSAFE = re.compile(r'[^A-Za-z0-9_@-]')
_CODE = re.compile(r'^[a-z0-9_]{1,64}$')

# A network's result, by its `error_code` (the docs' "What to do").
SKIP_NOT_CONFIGURED = 'profile_platform_not_configured'
CODES_NOT_CONNECTED = frozenset({SKIP_NOT_CONFIGURED, 'profile_platform_mapping_invalid', 'profile_not_found'})
# "Reconnect the account".
CODES_RELINK = frozenset({'account_reauth_required', 'account_permission_denied'})
# The allowance of the month, or of the day for that account or that network.
CODES_QUOTA = frozenset({
    'daily_cap_reached', 'monthly_limit_reached', 'instagram_publishing_limit_reached',
    'youtube_channel_daily_limit', 'youtube_api_quota_exceeded', 'reached_active_user_cap',
})
# The account cannot publish with that privacy level: nothing was sent to TikTok.
CODE_TIKTOK_PRIVACY = 'tiktok_privacy_unavailable'
CODES_DUPLICATE = frozenset({'content_duplicate'})
# "Check the account before posting again": it may be out (TikTok had accepted the job).
CODES_MAYBE_OUT = frozenset({'upload_interrupted', 'x_unknown_outcome', 'tiktok_publish_failed'})
# "Retry later": nothing went out. The job is spent (its key answers the same failure for 24 h).
CODES_LATER = frozenset({
    'platform_rate_limited', 'platform_unavailable', 'platform_integration_paused', 'cost_cap_reached',
    'media_download_failed',
})
CODE_KEY_IN_USE = 'idempotency_key_in_use'

# Statuses of a request or of one network in it.
NOT_FINAL = frozenset({'pending', 'queued', 'processing', 'in_progress', 'retryable'})
COMPLETED = 'completed'
FAILED = 'failed'

# The clock, for the tests.
_clock = time.monotonic
_sleep = time.sleep


def profile_username(workspace):
    """
    The profile's username at Upload-Post: the same for every try, so a creation whose answer
    was lost answers 409 and is the same profile. It cannot be changed later. The prefix tells
    environments apart when they share an Upload-Post account.
    """
    return _USERNAME_UNSAFE.sub('-', f"{settings.SOCIAL_PROFILE_TITLE_PREFIX}-{workspace.pk}")


def _url(path):
    """UPLOAD_POST_BASE_URL (https in production: backend.settings.prod checks it) and the path."""
    return f"{settings.UPLOAD_POST_BASE_URL.rstrip('/')}{path}"


class _NoAnswer(Exception):
    """The request may have reached Upload-Post and no answer came. Never leaves this module."""

    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


class UploadPostProvider(SocialProvider):
    name = 'upload_post'
    display_name = 'Upload-Post'

    # --- Profiles ---

    def create_profile(self, workspace):
        username = profile_username(workspace)
        try:
            response = self._send('post', '/api/uploadposts/users', 'create_profile', json={'username': username})
        except _NoAnswer as exc:
            # Asking again is safe: an existing username answers 409, the same profile.
            raise RetryableError(detail=exc.detail, maybe_sent=True) from exc
        if response.status_code == 409:
            logger.info("social.upload_post.profile_exists")
            return CreatedProfile(ref=username, provider_id=username)
        self._body_or_raise(response, 'create_profile')
        return CreatedProfile(ref=username, provider_id=username)

    def connect_url(self, profile, platform, return_url=None):
        body = {
            'username': _username(profile),
            'platforms': [NETWORK[platform]],
            'language': 'es',
            'connect_title': LINKING_PAGE_TITLE,
            # The page shows the profile's calendar by default: OneCreator schedules its own posts.
            'show_calendar': False,
        }
        if return_url:
            # Already checked by the API: the scheme and host of FRONTEND_URL (no open redirect).
            body['redirect_url'] = return_url
            body['redirect_button_text'] = LINKING_PAGE_BACK
        data = self._call('post', '/api/uploadposts/users/generate-jwt', 'connect_url', json=body)
        url = data.get('access_url')
        if not isinstance(url, str) or not url.startswith(link_schemes(settings.UPLOAD_POST_BASE_URL)):
            raise PermanentError(PROVIDER_REJECTED, 'upload-post generate-jwt answered no access_url')
        return url

    def accounts(self, profile):
        username = _username(profile)
        data = self._call(
            'get', f'/api/uploadposts/users/{quote(username, safe="")}', 'accounts', timeout=ACCOUNTS_TIMEOUT_SECONDS,
        )
        found = (data.get('profile') if isinstance(data.get('profile'), dict) else {}).get('social_accounts')
        linked = found if isinstance(found, dict) else {}
        accounts = []
        for platform in PLATFORMS:
            entry = linked.get(NETWORK[platform])
            if not _is_linked(entry):
                accounts.append(Account(platform, AccountStatus.DISCONNECTED))
                continue
            status = AccountStatus.EXPIRED if entry.get('reauth_required') is True else AccountStatus.CONNECTED
            creator = None
            if platform == TIKTOK and status == AccountStatus.CONNECTED:
                creator, reauth = self._tiktok_creator(username)
                if reauth:
                    status = AccountStatus.EXPIRED
            accounts.append(Account(platform, status, _shown_name(platform, entry), None, creator))
        return accounts

    def disconnect(self, profile, platform):
        body = {'profile_username': _username(profile), 'social_platform': NETWORK[platform]}
        try:
            self._call('delete', '/api/uploadposts/users/social', 'disconnect', json=body)
        except PermanentError as exc:
            if exc.code != PROFILE_NOT_FOUND:  # 404: never linked, or no profile: nothing to unlink
                raise

    def delete_profile(self, profile):
        if not getattr(profile, 'provider_ref', ''):
            return  # nothing at the provider to delete
        try:
            self._call('delete', '/api/uploadposts/users', 'delete_profile', json={'username': _username(profile)})
        except PermanentError as exc:
            if exc.code != PROFILE_NOT_FOUND:  # 404: already gone
                raise

    def _tiktok_creator(self, username):
        """
        (TikTok's creator info in our terms, or None; whether TikTok asks to reconnect). Never an
        error: without it the API offers the safe default (views_social_accounts).
        """
        try:
            response = self._send(
                'get', '/api/uploadposts/tiktok/settings', 'tiktok_settings',
                params={'profile': username}, timeout=ACCOUNTS_TIMEOUT_SECONDS,
            )
        except (SocialProviderError, _NoAnswer) as exc:
            logger.warning("social.upload_post.tiktok_settings_failed error=%s", type(exc).__name__)
            return None, False
        body = json_object(response) or {}
        if not 200 <= response.status_code < 300 or body.get('success') is False:
            logger.warning("social.upload_post.tiktok_settings_failed http=%s", response.status_code)
            return None, body.get('reauth_required') is True
        legacy = {'FOLLOWER_ONLY': 'FOLLOWER_OF_CREATOR'}
        options = []
        for level in as_list(body.get('privacy_level_options')):
            level = legacy.get(level, level)
            if level in TIKTOK_PRIVACY_LEVELS and level not in options:
                options.append(level)
        if not options:
            return None, False
        return {
            'privacy_options': options,
            'comment_disabled': body.get('comment_disabled') is True,
            'duet_disabled': body.get('duet_disabled') is True,
            'stitch_disabled': body.get('stitch_disabled') is True,
        }, False

    # --- Publishing ---

    def publish(self, profile, request):
        username = _username(profile)
        network = NETWORK[request.platform]
        fields = _upload_fields(username, network, request)
        deadline = _clock() + PUBLISH_DEADLINE_SECONDS
        try:
            result = self._publish(network, request.idempotency_key, fields, deadline)
        except SocialProviderError as exc:
            logger.warning(
                "social.upload_post.publish_failed platform=%s error=%s code=%s detail=%s",
                request.platform, type(exc).__name__, exc.code, exc.detail,
            )
            raise
        logger.info("social.upload_post.published platform=%s pending=%s", request.platform, result.pending)
        return result

    def _publish(self, network, key, fields, deadline):
        try:
            response = self._send(
                'post', '/api/upload', 'publish', files=fields, headers={'Idempotency-Key': key},
                timeout=UPLOAD_TIMEOUT_SECONDS,
            )
        except _NoAnswer as exc:
            # Sent, and then no answer: it may be out. Its status says, under our own request_id.
            return self._settle(network, key, deadline, exc.detail)
        outcome = _upload_outcome(response, network, key)
        if isinstance(outcome, PublishResult):
            return outcome
        request_id, why = outcome
        return self._settle(network, request_id, deadline, why)

    def _settle(self, network, request_id, deadline, why):
        """
        Reads the status of ``request_id`` until the network's result, or the deadline:
        OutcomeUnknownError then. The upload may be out, so nothing here is ever "nothing
        went out": a status that cannot be read is asked again, never reported as a refusal.
        """
        seen = 'none'
        while True:
            left = deadline - _clock()
            if left <= 0:
                break
            _sleep(min(STATUS_POLL_SECONDS, left))
            left = deadline - _clock()
            if left <= 0:
                break
            try:
                response = self._send(
                    'get', '/api/uploadposts/status', 'status', params={'request_id': request_id},
                    timeout=(STATUS_TIMEOUT_SECONDS[0], max(1, min(STATUS_TIMEOUT_SECONDS[1], left))),
                )
            except (SocialProviderError, _NoAnswer) as exc:
                seen = type(exc).__name__
                continue
            http = response.status_code
            if http in (401, 403, 400):
                seen = f'http={http}'
                break  # it cannot be read with this key or this id: asking again changes nothing
            body = json_object(response)
            if not 200 <= http < 300 or body is None:
                seen = f'http={http}'
                wait = _retry_after(response, body) if http == 429 else None
                if wait:
                    _sleep(max(0, min(wait, deadline - _clock())))
                continue
            seen = _code(body.get('status')) or 'unreadable'
            result = _status_outcome(body, network, request_id)
            if result is not None:
                return result
        logger.error("social.upload_post.unconfirmed why=%s status=%s", why, seen)
        raise OutcomeUnknownError(detail=f'upload-post {why}; status={seen}; request_id={_safe_id(request_id)}')

    # --- HTTP ---

    def _call(self, method, path, op, json=None, params=None, timeout=ADMIN_TIMEOUT_SECONDS):
        """A call other than publishing: its answer's body, or the error by HTTP status and error_code."""
        try:
            response = self._send(method, path, op, json=json, params=params, timeout=timeout)
        except _NoAnswer as exc:
            raise RetryableError(detail=exc.detail, maybe_sent=True) from exc
        return self._body_or_raise(response, op)

    def _body_or_raise(self, response, op):
        http = response.status_code
        body = json_object(response) or {}
        if 200 <= http < 300 and body.get('success') is not False:
            return body
        codes = _codes([body])
        detail = _detail(http, codes)
        logger.warning("social.upload_post.call_failed op=%s detail=%s", op, detail)
        if http == 404 or 'profile_not_found' in codes:
            raise PermanentError(PROFILE_NOT_FOUND, detail)
        if http == 401:
            raise PermanentError(PROVIDER_NOT_CONFIGURED, detail)
        if http == 429:
            raise RetryableError(PROVIDER_UNAVAILABLE, detail, retry_after=_retry_after(response, body))
        if http >= 500:
            raise RetryableError(PROVIDER_UNAVAILABLE, detail, maybe_sent=True)
        raise PermanentError(PROVIDER_REJECTED, detail)

    def _send(self, method, path, op, json=None, params=None, files=None, headers=None, timeout=ADMIN_TIMEOUT_SECONDS):
        """
        The response, or: RetryableError when nothing reached Upload-Post (no connection, no name,
        a proxy or TLS handshake that failed); _NoAnswer when the request may have reached it
        without an answer (a read timeout, a connection cut after sending, a response cut halfway).
        """
        api_key = getattr(settings, 'UPLOAD_POST_API_KEY', None)
        if not api_key:
            logger.error("social.upload_post.not_configured op=%s", op)
            raise PermanentError(PROVIDER_NOT_CONFIGURED, 'UPLOAD_POST_API_KEY missing')
        sent_headers = {'Authorization': f'Apikey {api_key}', **(headers or {})}
        try:
            response = requests.request(
                method, _url(path), json=json, params=params, files=files, headers=sent_headers, timeout=timeout,
            )
        except requests.exceptions.RequestException as exc:
            detail = f'upload-post {type(exc).__name__}'
            if never_sent(exc):
                raise RetryableError(detail=detail) from exc
            logger.error("social.upload_post.no_answer op=%s error=%s", op, type(exc).__name__)
            raise _NoAnswer(detail) from exc
        logger.info("social.upload_post.call op=%s http=%s", op, response.status_code)
        return response


# --- What a post sends ---

def _upload_fields(username, network, request):
    """The multipart fields of POST /upload, as (name, (None, value)): requests then sends multipart."""
    fields = [
        ('user', username),
        ('platform[]', network),
        # The presigned URL: Upload-Post downloads the video from it.
        ('video', request.media_url),
        # Ours, so its status can be read even if the answer is lost (the same for every attempt of a key).
        ('request_id', request.idempotency_key),
    ]
    text = request.text or ''
    if request.platform == YOUTUBE_SHORTS:
        title = youtube_title(request.title)
        fields += [('title', title), ('youtube_title', title), ('privacyStatus', 'public')]
        if text:
            fields.append(('youtube_description', text))
    else:
        # TikTok's and Instagram's caption is their title. Empty: none at all, nothing made up.
        if text:
            fields += [('title', text), (f'{network}_title', text)]
        if request.platform == TIKTOK:
            fields += _tiktok_fields(request.tiktok)
        else:
            fields.append(('media_type', 'REELS'))
    return [(name, (None, value)) for name, value in fields]


def _tiktok_fields(options):
    if options is None:
        raise PermanentError(TIKTOK_OPTIONS_MISSING)
    return [
        ('privacy_level', options.privacy_level),
        ('disable_comment', _bool(not options.allow_comment)),
        ('disable_duet', _bool(not options.allow_duet)),
        ('disable_stitch', _bool(not options.allow_stitch)),
        ('brand_organic_toggle', _bool(options.brand_organic)),
        ('brand_content_toggle', _bool(options.branded_content)),
        # Over TikTok's daily cap, a post would land in the account's drafts and answer success: a
        # clear quota error instead (inbox drafts cannot be deleted through TikTok's API).
        ('disable_inbox_fallback', 'true'),
    ]


def _bool(value):
    return 'true' if value else 'false'


# --- Reading an answer ---

def _upload_outcome(response, network, key):
    """
    PublishResult, the error of errors.py the answer means, or (request_id, why) when the result
    is still to come: then its status says.
    """
    http = response.status_code
    body = json_object(response)
    request_id = text_value((body or {}).get('request_id')) or key
    results = (body or {}).get('results')
    entry = _entry(results, network)
    if entry is not None:
        result = _platform_result(entry, request_id, final=True)
        if result is not None:
            return result
        return request_id, 'queued'
    if 200 <= http < 300:
        if body is None or body.get('success') is not False:
            # In the background (request_id, no results), or an answer we cannot read: it may be out.
            return request_id, 'async' if body is not None and body.get('request_id') else 'unreadable'
    codes = _codes([body or {}])
    detail = _detail(http, codes)
    invalid = (body or {}).get('invalid_platforms')
    if (isinstance(invalid, dict) and network in invalid) or 'profile_not_found' in codes:
        raise PermanentError(ACCOUNT_NOT_CONNECTED, detail)
    known = _known_error(codes, detail)
    if known is not None:
        raise known
    if CODE_KEY_IN_USE in codes:
        # An attempt with this key is still being made: the same key later answers its job.
        raise RetryableError(PROVIDER_UNAVAILABLE, detail, retry_after=KEY_IN_USE_WAIT_SECONDS, maybe_sent=True)
    if http == 429:
        if _allowance_used(response, body):
            raise PermanentError(QUOTA_EXCEEDED, detail)
        # The rate limit refuses the request before it is processed: the same key again.
        raise RetryableError(PROVIDER_UNAVAILABLE, detail, retry_after=_retry_after(response, body))
    if http == 503 and _retry_after(response, body):
        # Down for a while, and it says how long: refused before anything was processed.
        raise RetryableError(PROVIDER_UNAVAILABLE, detail, retry_after=_retry_after(response, body))
    if http == 504:
        return request_id, detail.replace('upload-post ', '')
    if http >= 500:
        # It reached Upload-Post and we do not know what it did: the same key again, which answers
        # the original job if there is one.
        raise RetryableError(PROVIDER_UNAVAILABLE, detail, maybe_sent=True)
    if http == 401:
        raise PermanentError(PROVIDER_NOT_CONFIGURED, detail)
    raise PermanentError(PROVIDER_REJECTED, detail)


def _status_outcome(body, network, request_id):
    """PublishResult, the network's error, or None while the request is not finished."""
    state = _code(body.get('status'))
    entry = _entry(body.get('results'), network)
    if entry is not None:
        own = _code(entry.get('status'))
        # An entry says `success: true` while it is still queued: only its status, or a finished
        # request, makes it final.
        final = own in (COMPLETED, FAILED) or (own is None and state in (COMPLETED, FAILED))
        result = _platform_result(entry, request_id, final=final)
        if result is not None:
            return result
    if state in (COMPLETED, FAILED) and entry is None:
        # Finished, without a word on this network: it cannot be confirmed, nor told to try again.
        raise OutcomeUnknownError(detail=f'upload-post status={state} without {network}')
    return None


def _platform_result(entry, request_id, final):
    """
    One network's entry: PublishResult when it is out, its error when it failed, None when it is
    not final yet (queued, processing, retryable, or a status entry without its own status).
    """
    if entry.get('skipped') is True or entry.get('skip_reason') == SKIP_NOT_CONFIGURED:
        raise PermanentError(ACCOUNT_NOT_CONNECTED, _detail(None, _codes([entry]) or [SKIP_NOT_CONFIGURED]))
    own = _code(entry.get('status'))
    if own in NOT_FINAL or not final:
        return None
    if entry.get('success') is True and own in (None, COMPLETED):
        return _published(entry, request_id)
    if entry.get('success') is False or own == FAILED:
        raise _platform_error(entry)
    return None


def _platform_error(entry):
    codes = _codes([entry])
    detail = _detail(None, codes)
    known = _known_error(codes, detail)
    if known is not None:
        return known
    if CODES_LATER & set(codes):
        # Upload-Post says nothing went out and it may work later; this job is spent.
        return RetryableError(PROVIDER_UNAVAILABLE, detail, key_spent=True)
    return PermanentError(PROVIDER_REJECTED, detail)


def _known_error(codes, detail):
    """The documented codes that decide by themselves, in a request's answer or a network's result."""
    found = set(codes)
    if CODES_NOT_CONNECTED & found:
        return PermanentError(ACCOUNT_NOT_CONNECTED, detail)
    if CODES_RELINK & found:
        return PermanentError(ACCOUNT_EXPIRED, detail)
    if CODES_QUOTA & found:
        return PermanentError(QUOTA_EXCEEDED, detail)
    if CODE_TIKTOK_PRIVACY in found:
        # Nothing was sent to TikTok. The post's privacy level is not one this account has (it
        # changed since): created again, the panel offers the account's own (accounts()).
        return PermanentError(TIKTOK_OPTIONS_MISSING, detail)
    if CODES_DUPLICATE & found:
        return PermanentError(DUPLICATE_CONTENT, detail)
    if CODES_MAYBE_OUT & found:
        return OutcomeUnknownError(detail=detail)
    return None


def _published(entry, request_id):
    url = entry.get('url') or entry.get('post_url')
    post_url = url if isinstance(url, str) and url.startswith('https://') and len(url) <= 500 else None
    # Upload-Post's id of the upload (ours when it kept it): its status and history are found by it.
    external_id = text_value(request_id)
    if entry.get('fallback_to_inbox') is True:
        # In the account's TikTok drafts, not public: out of our hands all the same.
        logger.warning("social.upload_post.sent_to_inbox")
    return PublishResult(
        external_id=external_id[:255] if external_id else None,
        post_url=post_url,
        pending=post_url is None,
    )


# --- Small readers (never trust the shape of an answer) ---

def _username(profile):
    """The profile's username, or account_not_connected: no call is made without one."""
    ref = getattr(profile, 'provider_ref', '') or ''
    if not ref:
        raise PermanentError(ACCOUNT_NOT_CONNECTED, 'no profile')
    return ref


def _entry(results, network):
    """The network's entry of `results`: a dict keyed by network (the upload) or a list (the status)."""
    if isinstance(results, dict):
        entry = results.get(network)
        return entry if isinstance(entry, dict) else None
    for entry in as_list(results):
        if isinstance(entry, dict) and entry.get('platform') == network:
            return entry
    return None


def _is_linked(entry):
    return isinstance(entry, dict) and any(entry.get(name) for name in ('handle', 'username', 'display_name'))


def _shown_name(platform, entry):
    """The public @handle (never `username`: the network's opaque id); YouTube may show its channel's name."""
    handle = entry.get('handle')
    if isinstance(handle, str) and handle.strip():
        handle = handle.strip()
        return handle if handle.startswith('@') else f'@{handle}'
    name = entry.get('display_name')
    return name.strip() if isinstance(name, str) and name.strip() else None


def _code(value):
    """A code as Upload-Post documents them (lowercase, letters and "_"), or None: never free text."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value if _CODE.match(value) else None


def _codes(items):
    codes = set()
    for item in items:
        if isinstance(item, dict):
            for name in ('error_code', 'skip_reason'):
                code = _code(item.get(name))
                if code:
                    codes.add(code)
    return sorted(codes)


def _detail(http, codes):
    parts = ['upload-post'] + ([f'http={http}'] if http is not None else []) + ([f"code={','.join(codes)}"] if codes else [])
    return ' '.join(parts)


def _safe_id(value):
    return re.sub(r'[^A-Za-z0-9_.@-]', '', str(value))[:80]


def _retry_after(response, body):
    for value in ((response.headers or {}).get('Retry-After'), (body or {}).get('retry_after'),
                  (body or {}).get('retry_after_seconds')):
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            return seconds
    return None


def _allowance_used(response, body):
    """
    A 429 for the month's allowance carries the account's `usage` and no time to come back ("this
    upload would exceed your monthly limit"); the rate limit says when to come back.
    """
    return isinstance((body or {}).get('usage'), dict) and _retry_after(response, body) is None
