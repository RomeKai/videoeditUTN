"""
Attempt limits of the endpoints that check a password: the login, the token
refresh and the account endpoints that ask for `current_password`.

Without them a password can be guessed as fast as the server answers. The
rates are in REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'] and are counted in the
default cache: per user when signed in, per IP otherwise.
"""
import math
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils.crypto import salted_hmac
from rest_framework.exceptions import Throttled
from rest_framework.throttling import BaseThrottle, ScopedRateThrottle


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


RATE_UNITS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}


def parse_rate(rate):
    """
    `"10/15m"` → `(10, 900)`: requests per window. The window is a unit
    (s, m, h, d) with an optional count in front; `"100/h"` is one hour.
    Raises ValueError for anything else.
    """
    try:
        number, window = rate.split('/')
        count = int(number)
        size = int(window[:-1] or 1)
        unit = RATE_UNITS[window[-1]]
    except (AttributeError, ValueError, KeyError, IndexError):
        raise ValueError(f'Invalid rate {rate!r}: use "<count>/<window>", like "10/15m" or "100/h".')
    if count < 1 or size < 1:
        raise ValueError(f'Invalid rate {rate!r}: the count and the window must be positive.')
    return count, size * unit


class LoginFailureGuard:
    """
    Failed logins of one account. Only failures count, and a successful login
    clears them, so nobody can lock a user out with requests that succeed,
    nor with fewer failures than the limits:

    - from one IP: LOGIN_FAILURE_RATES['account_ip'] (10 every 15 minutes);
    - from any IP: LOGIN_FAILURE_RATES['account'] (100 an hour), so spreading
      the guesses over many addresses does not help either.

    Each attempt is counted before the password is checked, with the cache's
    atomic add + incr (an INCR in Redis), and given back if it did not fail:
    simultaneous attempts cannot all slip under the limit. The windows are
    fixed (they start at multiples of their length), so right at the border
    up to twice the limit can go through in a short time.

    It uses the `default` cache, which fails open: with Redis down every
    count reads as a miss and the login only has the password check (and the
    per-IP throttle, which fails open too). Locking everyone out during an
    outage would be worse. The keys are an HMAC of the account (and the IP),
    never the email.
    """

    SALT = 'apps.users.throttles.login'

    def __init__(self, request, email):
        account = self.account_key(email)
        ip = BaseThrottle().get_ident(request) or ''
        rates = settings.LOGIN_FAILURE_RATES
        self.windows = [
            (f'login_failures:{salted_hmac(self.SALT, f"{account}|{ip}").hexdigest()}', *parse_rate(rates['account_ip'])),
            (f'login_failures:{salted_hmac(self.SALT, account).hexdigest()}', *parse_rate(rates['account'])),
        ]
        self.counted = []

    @staticmethod
    def account_key(email):
        """
        The account the login would sign into, found like the login finds it
        (`email__iexact`): every spelling of one email ("Tester@", "teſter@")
        shares its counters. Without an account, the typed email in upper case.
        """
        typed = email.strip()
        pk = get_user_model().objects.filter(email__iexact=typed).values_list('pk', flat=True).first()
        return f'user:{pk}' if pk is not None else f'email:{typed.upper()}'

    @classmethod
    def for_request(cls, request):
        data = request.data
        email = data.get('email') if hasattr(data, 'get') else None
        return cls(request, email) if isinstance(email, str) and email.strip() else None

    @staticmethod
    def _count(key, timeout):
        """This attempt's number in its window, or 0 when the cache does not answer."""
        try:
            cache.add(key, 0, timeout)
            count = cache.incr(key)
        except ValueError:
            # The key expired between add and incr: this is the window's first attempt.
            cache.add(key, 1, timeout)
            count = 1
        return count if isinstance(count, int) else 0

    def reserve(self):
        """
        Counts the attempt in every window. Returns 0 when it may go on, or the
        seconds until the fullest window ends.
        """
        now = time.time()
        waits = []
        for base, count, duration in self.windows:
            window = int(now // duration)
            ends = (window + 1) * duration
            key = f'{base}:{window}'
            number = self._count(key, int(ends - now) + 1)
            self.counted.append(key)
            if number > count:
                waits.append(ends - now)
        return max(waits, default=0)

    def release(self):
        """The attempt did not reach the password check (a 400, an error): it does not count."""
        for key in self.counted:
            try:
                cache.decr(key)
            except ValueError:
                pass

    def succeeded(self):
        cache.delete_many(self.counted)
