"""
Attempt limits of the account endpoints that check the current password.

Without them a stolen access token could guess `current_password` as fast as
the server answers. The rates are in REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
and are counted per user in the default cache.
"""
import math

from rest_framework.exceptions import Throttled
from rest_framework.throttling import ScopedRateThrottle


class TooManyAttempts(Throttled):
    """429 with a sentence in Spanish: the web app shows it as it comes (`errorMessage`)."""

    def __init__(self, wait=None):
        wait = math.ceil(wait) if wait else None
        if wait:
            minutes = max(1, math.ceil(wait / 60))
            detail = f'Demasiados intentos. Probá de nuevo en {minutes} minuto{"" if minutes == 1 else "s"}.'
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
