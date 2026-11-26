"""A cache backend whose server is down: every call raises, like django-redis without Redis."""
from django.core.cache.backends.base import BaseCache

UNREACHABLE_CACHES = {'default': {'BACKEND': 'apps.payments.tests.unreachable_cache.UnreachableCache'}}


class UnreachableCache(BaseCache):
    def __init__(self, location, params):
        super().__init__(params)

    def _down(self, *args, **kwargs):
        raise ConnectionError('cache server unreachable')

    add = get = set = touch = delete = clear = has_key = incr = decr = _down
    get_many = set_many = delete_many = _down
