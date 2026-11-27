"""A clock that does not move, for the tests that count attempts inside a window."""
from contextlib import contextmanager
from unittest import mock

from rest_framework.throttling import ScopedRateThrottle

# A fixed instant, 37 s into a 15-minute window (and into its hour).
NOW = 1_800_000_037.0


@contextmanager
def frozen_clocks():
    """
    Time does not pass inside, however slow the machine is. The login limits
    count attempts inside windows (a minute per IP, 15 minutes and an hour per
    account) and inside the 5 s cooldown of the cache's circuit breaker: on a
    loaded runner a handful of logins (each one hashes a password) took longer
    than a cooldown or than the minute, or crossed the edge of a window, and
    the count started again.

    - the breaker's clock (apps.core.cache._now);
    - DRF's throttle timer (bound to time.time when the class was defined);
    - time.time, which LoginFailureGuard and LocMem's expiry read.

    A test that needs time to move patches time.time again inside.
    """
    with mock.patch("apps.core.cache._now", return_value=1000.0), \
            mock.patch.object(ScopedRateThrottle, "timer", return_value=NOW), \
            mock.patch("time.time", return_value=NOW):
        yield
