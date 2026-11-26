"""
Attempt limits of the endpoints that check a password: the login, the token
refresh and the account endpoints that ask for `current_password`.

Without them a password can be guessed as fast as the server answers. The
rates are in REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'] and are counted in the
default cache: per user when signed in, per IP otherwise.
"""
import hashlib
import math

from rest_framework.exceptions import Throttled
from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle


class TooManyAttempts(Throttled):
    """429 with a sentence in Spanish: the web app shows it as it comes (`errorMessage`)."""

    def __init__(self, wait=None):
        wait = math.ceil(wait) if wait else None
        if wait and wait < 120:
            detail = f'Demasiados intentos. Probá de nuevo en {wait} segundo{"" if wait == 1 else "s"}.'
        elif wait:
            minutes = math.ceil(wait / 60)
            detail = f'Demasiados intentos. Probá de nuevo en {minutes} minutos.'
        else:
            detail = 'Demasiados intentos. Probá de nuevo más tarde.'
        super().__init__(detail=detail)
        # Throttled only appends the English "Expected available in..." when it gets the wait,
        # so it is set afterwards: the exception handler still sends Retry-After.
        self.wait = wait


class SpanishThrottleMixin:
    """For an APIView: the 429 of its throttles says, in Spanish, how long to wait."""

    def throttled(self, request, wait):
        raise TooManyAttempts(wait)


class EmailChangeThrottle(ScopedRateThrottle):
    """
    Only counts the PATCHes of `/me` that change the email (they check the
    password and send an email). The rest of `/me` stays unlimited: the web app
    patches `active_workspace_id` on every workspace switch.
    """

    def allow_request(self, request, view):
        if request.method != 'PATCH':
            return True
        data = request.data
        if not hasattr(data, 'keys') or not ({'email', 'current_password'} & set(data.keys())):
            return True
        return super().allow_request(request, view)


class LoginEmailThrottle(SimpleRateThrottle):
    """
    Login attempts per account (the email of the request), on top of the limit
    per IP: guessing one account's password from many addresses is limited too.
    The cache key carries a hash of the email, not the email.
    """

    scope = 'login_email'

    def get_cache_key(self, request, view):
        data = request.data
        email = data.get('email') if hasattr(data, 'get') else None
        if not isinstance(email, str) or not email.strip():
            return None
        digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
        return self.cache_format % {'scope': self.scope, 'ident': digest}
