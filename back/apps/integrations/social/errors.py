"""
Errors of the social publishing providers, by what the caller may do next.

- RetryableError: worth sending again with backoff. Either nothing reached
  the provider (no connection, a rate limit), or it answered for certain that
  nothing went out and it may work later (`key_spent`), or a 5xx did not say
  what happened (`maybe_sent`: the publisher then keeps the same idempotency
  key until it gets a clear answer, and never tells the user to retry).
- PermanentError: nothing was published and sending it again as it is will
  not change that. Never retried; `code` says why.
- OutcomeUnknownError: the request may have published the post (a read
  timeout, a connection cut after the request was sent, a 504). Never retried
  automatically: the provider may still be working on it (Ayrshare downloads
  the video inside the request), and nothing documents that it refuses a key
  still in flight. A person checks the network and decides ("Reintentar").

`code` is one of the codes below: the API maps it to a sentence in Spanish
(serializers_social.PUBLISH_ERROR_MESSAGES). `detail` is for error_log and
the logs: the HTTP status, the provider's numeric codes or an exception class,
never the provider's message (it can quote the caption) nor a profile key.
"""

# The account of that network is not connected to the workspace's profile (or there is no profile).
ACCOUNT_NOT_CONNECTED = 'account_not_connected'
# The clip lasts more than the network accepts (scheduled_posts.MAX_DURATION_SECONDS): never sent.
CLIP_TOO_LONG = 'clip_too_long'
# The network asks to connect the account again (its authorization expired or was revoked).
ACCOUNT_EXPIRED = 'account_expired'
# The provider's monthly post quota, or the network's daily cap, is used up.
QUOTA_EXCEEDED = 'quota_exceeded'
# A TikTok post without the options TikTok audits (privacy_level at least).
TIKTOK_OPTIONS_MISSING = 'tiktok_options_missing'
# The same content went out on that network recently: this one may already be public.
DUPLICATE_CONTENT = 'duplicate_content'
# The network or the provider refused the post (media, text, account state...).
PROVIDER_REJECTED = 'provider_rejected'
# The provider or the network kept failing for a temporary reason (the worker's retries ran out).
PROVIDER_UNAVAILABLE = 'provider_unavailable'
# Our side: no API key, or a provider this release does not know.
PROVIDER_NOT_CONFIGURED = 'provider_not_configured'
# The request may have published the post.
OUTCOME_UNKNOWN = 'outcome_unknown'

# Of the profiles, never a post's code: the provider has no profile with that key (Ayrshare's 144),
# and a new profile's title is taken (146: an earlier creation whose answer was lost).
PROFILE_NOT_FOUND = 'profile_not_found'
PROFILE_TITLE_TAKEN = 'profile_title_taken'

PUBLISH_ERROR_CODES = (
    ACCOUNT_NOT_CONNECTED, ACCOUNT_EXPIRED, QUOTA_EXCEEDED, TIKTOK_OPTIONS_MISSING, DUPLICATE_CONTENT,
    PROVIDER_REJECTED, PROVIDER_UNAVAILABLE, PROVIDER_NOT_CONFIGURED, OUTCOME_UNKNOWN,
)


class SocialProviderError(Exception):
    default_code = PROVIDER_REJECTED

    def __init__(self, code=None, detail=''):
        self.code = code or self.default_code
        self.detail = detail
        # The whole text is safe to store: a code and the detail, nothing the provider wrote.
        super().__init__(f"{self.code} ({detail})" if detail else self.code)


class RetryableError(SocialProviderError):
    """
    ``retry_after``: seconds the provider asked to wait, if it said.
    ``key_spent``: the provider answered this request (it may have stored its
    idempotency key) and said for certain that nothing went out: the next
    attempt needs a new key, or the provider would refuse it as a repeat.
    ``maybe_sent``: the request reached the provider and the answer did not
    say what happened (a 5xx without an explanation): it may have published.
    Neither: nothing reached the provider.
    """

    default_code = PROVIDER_UNAVAILABLE

    def __init__(self, code=None, detail='', retry_after=None, key_spent=False, maybe_sent=False):
        super().__init__(code, detail)
        self.retry_after = retry_after
        self.key_spent = key_spent
        self.maybe_sent = maybe_sent and not key_spent


class PermanentError(SocialProviderError):
    default_code = PROVIDER_REJECTED


class OutcomeUnknownError(SocialProviderError):
    default_code = OUTCOME_UNKNOWN
