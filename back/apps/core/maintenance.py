"""
Maintenance mode (page K5 of the web app).

With MAINTENANCE_MODE=True in the environment, `GET /api/v1/status/` says so
(with the window of MAINTENANCE_STARTS_AT and MAINTENANCE_ENDS_AT, ISO 8601,
when both are set) and every other `/api/` request answers 503 with only
`{detail}`. For the web app a 503 without `code` or `error` (this one) is a
maybe: it asks the status at once and shows K5 only if the status says
maintenance or is itself a 503, then asks again every 10 s until it says
otherwise. An endpoint that answers 503 itself (storage down, a queue that did
not take the job) sends `error`, and usually a `code`, so it is never read
as maintenance; most use 502 or 501 anyway, since older builds of the web app
read any 503 as maintenance. The admin keeps
working, and so do the paths of MAINTENANCE_EXEMPT_PATHS (webhooks, health
checks).
"""
import logging
from datetime import timezone as dt_timezone

from django.conf import settings
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

MAINTENANCE_DETAIL = 'Estamos haciendo mantenimiento. Volvé en un rato.'

logger = logging.getLogger(__name__)


def is_exempt(path):
    """
    The status endpoint and each MAINTENANCE_EXEMPT_PATHS entry, with or
    without the final slash (this runs before APPEND_SLASH), and what is
    below them.
    """
    path = path.rstrip('/')
    for entry in (reverse('system-status'), *settings.MAINTENANCE_EXEMPT_PATHS):
        entry = entry.rstrip('/')
        if entry and (path == entry or path.startswith(f'{entry}/')):
            return True
    return False


def _parse(value):
    if not value:
        return None
    try:
        parsed = parse_datetime(value)
    except ValueError:
        return None
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, dt_timezone.utc)
    return parsed


def maintenance_window():
    """`{starts_at, ends_at}` from the environment, or None when it is missing or does not make sense."""
    starts_at = _parse(settings.MAINTENANCE_STARTS_AT)
    ends_at = _parse(settings.MAINTENANCE_ENDS_AT)
    if starts_at is None or ends_at is None or ends_at <= starts_at:
        return None
    return {'starts_at': starts_at, 'ends_at': ends_at}


class MaintenanceModeMiddleware:
    """503 for the API while MAINTENANCE_MODE is on, except the status endpoint."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.MAINTENANCE_MODE and request.path.startswith('/api/') and not is_exempt(request.path):
            response = JsonResponse({'detail': MAINTENANCE_DETAIL}, status=503)
            window = maintenance_window()
            now = timezone.now()
            if window and window['ends_at'] > now:
                response['Retry-After'] = str(int((window['ends_at'] - now).total_seconds()) + 1)
            # Expected while it lasts: INFO here instead of Django's ERROR for every 5xx.
            logger.info('maintenance.rejected method=%s', request.method)
            response._has_been_logged = True
            return response
        return self.get_response(request)
