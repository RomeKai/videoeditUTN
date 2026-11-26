"""
Attempt limits of the account endpoints that check the current password.

Without them a stolen access token could guess `current_password` as fast as
the server answers. The rates are in REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
and are counted per user in the default cache.
"""
import math

from rest_framework.exceptions import Throttled


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

