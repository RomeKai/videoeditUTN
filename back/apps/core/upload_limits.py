"""
How much a request may weigh, by route.

- The video of a project (create, from-prompt, the PUT or PATCH that replaces
  it): ``MAX_UPLOAD_BYTES`` (2 GB unless the environment says otherwise).
  ``GET /api/v1/pricing/`` gives it to the browser (``upload_max_size_bytes``),
  which checks the file before sending it.
- The files of a brand kit (create, PUT, PATCH): each one its own cap
  (``BRAND_KIT_FILE_MAX_BYTES``, the same the web app checks), the request
  their sum.
- Any other request: ``MAX_REQUEST_BYTES`` (5 MB). Nothing else uploads a file;
  this keeps a request that reads ``request.POST`` before the authentication
  (the admin's login, through the CSRF check) from spooling a big body to disk.

The routes that take a file also allow ``UPLOAD_FORM_OVERHEAD_BYTES`` for the
other fields of the form and the multipart boundaries.

``UploadSizeLimitMiddleware`` checks the Content-Length before anything reads
the body: 413 ``{error, code, limit_bytes}``, with ``code`` ``file_too_large``
on the routes that take a file and ``request_too_large`` on the others. Django
never reads more than the Content-Length says, and nothing without one. The
file itself is checked again where it is validated, before anything is done
with it: ``VideoProjectSerializer.validate_source_file`` (the same 413, before
ffprobe, the storage and the reservation) and ``BrandKitSerializer`` (a 400 on
the field). The proxy in front lets through the same by route (the demo's
``docker/demo/nginx.conf``).
"""
import logging

from django.conf import settings
from django.http import JsonResponse
from django.urls import Resolver404, resolve
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)

MB = 1024 ** 2
DEFAULT_MAX_UPLOAD_BYTES = 2 * 1024 ** 3
DEFAULT_MAX_REQUEST_BYTES = 5 * MB
# What a creation sends besides the file: the other fields (the prompt, the
# criterion, the style...) and the multipart boundaries. Far below this.
UPLOAD_FORM_OVERHEAD_BYTES = MB
FILE_TOO_LARGE = 'file_too_large'
REQUEST_TOO_LARGE = 'request_too_large'

# Each file of a brand kit, as the web app checks it (front/src/features/brand-kits/kit.ts · FILE_RULES).
BRAND_KIT_FILE_MAX_BYTES = {
    'primary_font': 5 * MB,
    'secondary_font': 5 * MB,
    'watermark_logo': 2 * MB,
    'intro_video': 50 * MB,
    'outro_video': 50 * MB,
}

# (url name, method) of the routes that take a file.
VIDEO_UPLOAD_ROUTES = frozenset({
    ('project-list', 'POST'), ('project-from-prompt', 'POST'), ('project-detail', 'PUT'), ('project-detail', 'PATCH'),
})
BRAND_KIT_ROUTES = frozenset({('brandkit-list', 'POST'), ('brandkit-detail', 'PUT'), ('brandkit-detail', 'PATCH')})


def max_upload_bytes():
    return int(getattr(settings, 'MAX_UPLOAD_BYTES', DEFAULT_MAX_UPLOAD_BYTES))


def max_request_bytes():
    return int(getattr(settings, 'MAX_REQUEST_BYTES', DEFAULT_MAX_REQUEST_BYTES))


def brand_kit_max_bytes():
    """What all the files of a brand kit may weigh together."""
    return sum(BRAND_KIT_FILE_MAX_BYTES.values())


def format_bytes(size):
    """2147483648 → "2 GB", 1610612736 → "1,5 GB": the units of the web app (1 KB = 1024 B)."""
    for unit, power in (('GB', 3), ('MB', 2), ('KB', 1)):
        value = size / 1024 ** power
        if value >= 1:
            number = f'{value:.1f}'.rstrip('0').rstrip('.').replace('.', ',')
            return f'{number} {unit}'
    return f'{size} B'


def too_large_body(limit):
    return {
        'error': f'El archivo pesa más de {format_bytes(limit)}, el máximo que podemos recibir.',
        'code': FILE_TOO_LARGE,
        'limit_bytes': limit,
    }


def _brand_kit_too_large_body(limit):
    return {
        'error': f'Los archivos pesan más de {format_bytes(limit)}, el máximo que podemos recibir.',
        'code': FILE_TOO_LARGE,
        'limit_bytes': limit,
    }


def _request_too_large_body(limit):
    return {
        'error': f'El pedido pesa más de {format_bytes(limit)}, el máximo que podemos recibir.',
        'code': REQUEST_TOO_LARGE,
        'limit_bytes': limit,
    }


class UploadTooLarge(APIException):
    """The 413 of a video over the cap, raised where the file is validated."""

    status_code = 413
    default_code = FILE_TOO_LARGE

    def __init__(self, limit=None):
        super().__init__()
        # A dict is sent as it is, the same body as the middleware's (limit_bytes stays a number).
        self.detail = too_large_body(max_upload_bytes() if limit is None else limit)


def _content_length(request):
    try:
        return int(request.META.get('CONTENT_LENGTH') or 0)
    except (TypeError, ValueError):
        return 0  # Django reads nothing then either


def body_limit(request):
    """``(the most the body of this request may weigh, the 413 body over it)``, by its route and method."""
    try:
        name = resolve(request.path_info).url_name
    except Resolver404:
        name = None
    route = (name, request.method)
    if route in VIDEO_UPLOAD_ROUTES:
        return max_upload_bytes() + UPLOAD_FORM_OVERHEAD_BYTES, too_large_body(max_upload_bytes())
    if route in BRAND_KIT_ROUTES:
        return brand_kit_max_bytes() + UPLOAD_FORM_OVERHEAD_BYTES, _brand_kit_too_large_body(brand_kit_max_bytes())
    return max_request_bytes(), _request_too_large_body(max_request_bytes())


class UploadSizeLimitMiddleware:
    """413 for a body over the limit of its route, before any view or parser reads it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        length = _content_length(request)
        # The route is only looked up for a body over the smallest limit.
        floor = min(max_request_bytes(), max_upload_bytes(), brand_kit_max_bytes())
        if length > floor:
            limit, body = body_limit(request)
            if length > limit:
                # INFO: anyone can send the header, without a body.
                logger.info(
                    'upload.too_large method=%s code=%s content_length=%d limit=%d',
                    request.method, body['code'], length, limit,
                )
                response = JsonResponse(body, status=413)
                # Logged above, with the sizes: not again as Django's "Request Entity Too Large".
                response._has_been_logged = True
                return response
        return self.get_response(request)
