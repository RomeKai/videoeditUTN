"""
The size cap of an upload: ``MAX_UPLOAD_BYTES`` (2 GB unless the environment
says otherwise). ``GET /api/v1/pricing/`` gives it to the browser
(``upload_max_size_bytes``), which checks the file before sending it.

The server checks it twice, and both answer the same 413
``{error, code: "file_too_large", limit_bytes}``:

- ``UploadSizeLimitMiddleware``, before anything reads the body: a request
  whose Content-Length is over the cap plus ``UPLOAD_FORM_OVERHEAD_BYTES`` (the
  other fields of the form and the multipart boundaries). Django never reads
  more than the Content-Length says, and nothing without one, so this bounds
  what any request can make the server read, whatever the view.
- ``VideoProjectSerializer.validate_source_file``, with the size of the file
  itself: before ffprobe, before the file goes to storage and before the
  tokens are reserved.

The proxy in front should let through the same as the middleware (the demo's
``docker/demo/nginx.conf``: ``client_max_body_size``).
"""
import logging

from django.conf import settings
from django.http import JsonResponse
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)

DEFAULT_MAX_UPLOAD_BYTES = 2 * 1024 ** 3
# What a creation sends besides the file: the other fields (the prompt, the
# criterion, the style...) and the multipart boundaries. Far below this.
UPLOAD_FORM_OVERHEAD_BYTES = 1024 ** 2
FILE_TOO_LARGE = 'file_too_large'


def max_upload_bytes():
    return int(getattr(settings, 'MAX_UPLOAD_BYTES', DEFAULT_MAX_UPLOAD_BYTES))


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


class UploadTooLarge(APIException):
    """The 413 of a file over the cap, raised where the file is validated."""

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


class UploadSizeLimitMiddleware:
    """413 for a body over the upload cap, before any view or parser reads it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        length = _content_length(request)
        limit = max_upload_bytes()
        if length > limit + UPLOAD_FORM_OVERHEAD_BYTES:
            logger.warning('upload.too_large method=%s content_length=%d limit=%d', request.method, length, limit)
            response = JsonResponse(too_large_body(limit), status=413)
            # Logged above, with the sizes: not again as Django's "Request Entity Too Large".
            response._has_been_logged = True
            return response
        return self.get_response(request)
