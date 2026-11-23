"""
Own capacity control for the AI providers (AICORE-11, #60; ADR-012 D-C4).

Before any Groq or LLM call the pipeline reserves capacity in token buckets
kept in Valkey/Redis, so a demand peak becomes an orderly wait
(``pending_capacity``) instead of provider 429s.

Buckets
    One bucket per model and limited resource, configured in
    ``AI_CAPACITY_LIMITS`` below the provider's own limits (e.g. at 80 %)::

        {"groq/whisper-large-v3-turbo": {"audio_seconds_per_hour": 5760,
                                         "audio_seconds_per_day": 23040,
                                         "requests_per_minute": 16},
         "gemini/gemini-3.8-flash": {"tokens_per_minute": 160000,
                                     "requests_per_minute": 400}}

    A bucket holds at most its limit and refills continuously at
    ``limit / window``. A demand for ``audio_seconds`` draws from every
    ``audio_seconds_per_*`` bucket of the model, and so on for ``tokens`` and
    ``requests``.

Atomicity
    Reservations run as one Lua script with the server clock (``TIME``): all
    the buckets of a demand are checked and drawn together, or none is.
    N workers can never exceed a limit. A demand larger than a whole bucket
    waits for the bucket to be full and then drives it negative, delaying the
    next demands accordingly (the bucket refills, so it is never permanently
    negative).

Settlement
    Reservations are estimates. After the call, ``settle`` applies the real
    usage (audio seconds, tokens including reasoning, requests): the
    difference is refunded (capped at the limit) or drawn.

Failure policy
    If Valkey does not answer, the reservation raises the redis error: the
    pipeline treats it as retryable and the provider is never called without
    a reservation (same policy as the project lock, AICORE-7). Settlement
    never raises: the paid call already happened.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

KEY_PREFIX = "capacity:v1"
RESOURCES = ("audio_seconds", "tokens", "requests")
WINDOWS = {"minute": 60, "hour": 3600, "day": 86400}

# KEYS: bucket keys. ARGV: for each key, limit, refill per second and cost.
# Returns {1, "0"} when every bucket had room (all drawn) or {0, wait} with the
# seconds until the scarcest bucket can serve its cost (nothing drawn).
_RESERVE_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local levels = {}
local wait = 0
for i = 1, #KEYS do
  local base = (i - 1) * 3
  local limit = tonumber(ARGV[base + 1])
  local rate = tonumber(ARGV[base + 2])
  local cost = tonumber(ARGV[base + 3])
  local state = redis.call('HMGET', KEYS[i], 'tokens', 'ts')
  local tokens = tonumber(state[1])
  local ts = tonumber(state[2])
  if tokens == nil or ts == nil then
    tokens = limit
    ts = now
  end
  tokens = math.min(limit, tokens + math.max(0, now - ts) * rate)
  levels[i] = tokens
  local needed = math.min(cost, limit)
  if tokens < needed then
    wait = math.max(wait, (needed - tokens) / rate)
  end
end
if wait > 0 then
  return {0, tostring(wait)}
end
for i = 1, #KEYS do
  local base = (i - 1) * 3
  local limit = tonumber(ARGV[base + 1])
  local rate = tonumber(ARGV[base + 2])
  local left = levels[i] - tonumber(ARGV[base + 3])
  redis.call('HSET', KEYS[i], 'tokens', tostring(left), 'ts', tostring(now))
  redis.call('EXPIRE', KEYS[i], math.ceil((limit - left) / rate) + 1)
end
return {1, '0'}
"""

# KEYS[1]: bucket. ARGV: limit, refill per second, delta (>0 draws, <0 refunds).
_ADJUST_LUA = """
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
local limit = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local delta = tonumber(ARGV[3])
local state = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil or ts == nil then
  tokens = limit
  ts = now
end
tokens = math.min(limit, tokens + math.max(0, now - ts) * rate)
tokens = math.min(limit, tokens - delta)
redis.call('HSET', KEYS[1], 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('EXPIRE', KEYS[1], math.ceil((limit - tokens) / rate) + 1)
return tostring(tokens)
"""


class CapacityUnavailable(Exception):
    """
    No room in at least one bucket. Not a failure: the pipeline parks the
    stage in ``pending_capacity`` and re-queues the task after ``wait_seconds``.
    """

    def __init__(self, wait_seconds: float, stage: str = ""):
        super().__init__(f"capacity unavailable for {wait_seconds:.1f}s")
        self.wait_seconds = max(0.0, float(wait_seconds))
        self.stage = stage


class CapacityTimeoutError(Exception):
    """The project waited longer than ``AI_CAPACITY_MAX_WAIT_SECONDS``: final failure."""


@dataclass(frozen=True)
class Bucket:
    model: str
    resource: str
    window: str
    limit: float

    @property
    def key(self) -> str:
        return f"{KEY_PREFIX}:{self.model}:{self.resource}_per_{self.window}"

    @property
    def refill_per_second(self) -> float:
        return self.limit / WINDOWS[self.window]


@dataclass
class Reservation:
    """What was drawn, per bucket, so the real usage can be settled later."""

    drawn: List[Tuple[Bucket, float]] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.drawn


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def capacity_enabled() -> bool:
    return bool(getattr(settings, "AI_CAPACITY_ENABLED", False))


def buckets_for(model: str, resource: str) -> List[Bucket]:
    """
    The configured buckets of ``model`` for ``resource``. With capacity
    enabled, a model without any configured limit is a deployment error:
    calling it would bypass the capacity control.
    """
    limits = getattr(settings, "AI_CAPACITY_LIMITS", None) or {}
    model_limits = limits.get(model)
    if not isinstance(model_limits, Mapping):
        raise ImproperlyConfigured(
            f"AI_CAPACITY_LIMITS has no limits for {model!r}; every model the "
            "pipeline calls needs them while AI_CAPACITY_ENABLED is on."
        )
    buckets = []
    for window in WINDOWS:
        value = model_limits.get(f"{resource}_per_{window}")
        if value is None:
            continue
        try:
            limit = float(value)
        except (TypeError, ValueError):
            raise ImproperlyConfigured(
                f"AI_CAPACITY_LIMITS[{model!r}][{resource}_per_{window}] is not a number."
            ) from None
        if not math.isfinite(limit) or limit <= 0:
            raise ImproperlyConfigured(
                f"AI_CAPACITY_LIMITS[{model!r}][{resource}_per_{window}] must be positive."
            )
        buckets.append(Bucket(model, resource, window, limit))
    return buckets


# ---------------------------------------------------------------------------
# Store (Valkey / Redis)
# ---------------------------------------------------------------------------

class RedisCapacityStore:
    def __init__(self, client):
        self._reserve = client.register_script(_RESERVE_LUA)
        self._adjust = client.register_script(_ADJUST_LUA)

    def reserve(self, demands: Sequence[Tuple[Bucket, float]]) -> Tuple[bool, float]:
        keys = [bucket.key for bucket, _ in demands]
        args: List[str] = []
        for bucket, cost in demands:
            args += [repr(bucket.limit), repr(bucket.refill_per_second), repr(float(cost))]
        granted, wait = self._reserve(keys=keys, args=args)
        return int(granted) == 1, float(wait)

    def adjust(self, bucket: Bucket, delta: float) -> float:
        left = self._adjust(
            keys=[bucket.key],
            args=[repr(bucket.limit), repr(bucket.refill_per_second), repr(float(delta))],
        )
        return float(left)


def default_store() -> RedisCapacityStore:
    from apps.videos.services.ai.project_lock import get_redis_client

    return RedisCapacityStore(get_redis_client())


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

Demand = Mapping[str, Mapping[str, float]]  # model -> resource -> amount


class CapacityGate:
    """Reserves before a provider call and settles with the real usage after it."""

    def __init__(self, store=None):
        self._store = store if store is not None else default_store()

    def reserve(self, demand: Demand, stage: str = "") -> Reservation:
        drawn: List[Tuple[Bucket, float]] = []
        for model, resources in demand.items():
            for resource, amount in resources.items():
                if resource not in RESOURCES:
                    raise ValueError(f"Unknown capacity resource {resource!r}")
                amount = max(0.0, float(amount or 0))
                for bucket in buckets_for(model, resource):
                    drawn.append((bucket, amount))
        if not drawn:
            return Reservation()
        granted, wait = self._store.reserve(drawn)
        if not granted:
            raise CapacityUnavailable(wait, stage)
        return Reservation(drawn)

    def settle(self, reservation: Optional[Reservation], actual: Demand) -> None:
        """
        Real usage per model and resource. Buckets whose model/resource is not
        in ``actual`` keep the reservation (unknown usage: conservative). A
        model listed with 0 (e.g. an unused fallback) gets its share refunded.
        """
        if reservation is None or reservation.empty:
            return
        for bucket, reserved in reservation.drawn:
            used = (actual.get(bucket.model) or {}).get(bucket.resource)
            if used is None:
                continue
            delta = max(0.0, float(used)) - reserved
            if delta == 0:
                continue
            try:
                self._store.adjust(bucket, delta)
            except Exception as exc:  # the paid call already happened: never fail here
                logger.error(
                    "capacity.settle_failed bucket=%s error=%s", bucket.key, type(exc).__name__
                )
