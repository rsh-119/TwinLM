"""Shared state for several gateway instances, in Redis (`REDIS_URL`):
rate-limit windows, the exact-match response cache, budget reservations
(app.gateway.budgets), API-key revocation (app.auth) and admin-panel config
changes (app.gateway.runtime). Without REDIS_URL all of these are
in-process and one instance is the supported setup.

Still per instance with Redis: circuit breakers and latency/load stats
(each instance learns provider health from its own traffic), the semantic
cache, and the usage outbox (each instance writes its own rows to the
shared database).

If Redis is unreachable, limits and the cache fail *open* (requests are
served, unlimited and uncached) and the error is logged: an outage of the
coordination store must not take the gateway down.
"""
import json
import logging
import time
import uuid
from functools import lru_cache
from typing import Any, Optional, Tuple

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.config import get_settings
from app.gateway.cache import ResponseCache
from app.providers.base import TokenUsage
from app.router.model_router import RouteResult

logger = logging.getLogger("twinlm.shared")

PREFIX = "twinlm:"


DOWN_SECONDS = 10.0


class FailFastRedis(redis.Redis):
    """No retries, short timeouts, and after a connection error every call
    fails immediately for DOWN_SECONDS - these calls run on the request
    path, so an outage must cost microseconds per request, not seconds.
    Only plain commands and scripts are used (no pipelines), so every call
    goes through `execute_command`."""

    down_until = 0.0

    def execute_command(self, *args, **options):
        if time.monotonic() < self.down_until:
            raise redis.ConnectionError("redis marked down")
        try:
            return super().execute_command(*args, **options)
        except (redis.ConnectionError, redis.TimeoutError):
            self.down_until = time.monotonic() + DOWN_SECONDS
            raise


def connect(url: str) -> FailFastRedis:
    return FailFastRedis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5,
                                  retry=Retry(NoBackoff(), 0), health_check_interval=30)


@lru_cache
def get_redis() -> Optional[FailFastRedis]:
    """The Redis client, or None when REDIS_URL is unset."""
    url = get_settings().redis_url
    return connect(url) if url else None


# Sliding windows as sorted sets scored by Redis server time (ms), so
# instances with skewed clocks agree. Token entries are "count:uuid".
_ACQUIRE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local window = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', now - window)
local rpm, tpm = tonumber(ARGV[2]), tonumber(ARGV[3])
if rpm >= 0 and redis.call('ZCARD', KEYS[1]) >= rpm then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  return {1, tonumber(oldest[2]) + window - now}
end
if tpm >= 0 then
  local used = 0
  for _, member in ipairs(redis.call('ZRANGE', KEYS[2], 0, -1)) do
    used = used + tonumber(string.match(member, '^(%d+):'))
  end
  if used >= tpm then
    local oldest = redis.call('ZRANGE', KEYS[2], 0, 0, 'WITHSCORES')
    return {2, tonumber(oldest[2]) + window - now}
  end
end
redis.call('ZADD', KEYS[1], now, ARGV[4])
redis.call('PEXPIRE', KEYS[1], window)
return {0, 0}
"""
_RECORD = """
local t = redis.call('TIME')
redis.call('ZADD', KEYS[1], tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000), ARGV[1])
redis.call('PEXPIRE', KEYS[1], ARGV[2])
"""
_REASONS = {1: "requests_per_minute", 2: "tokens_per_minute"}


class RedisLimiter:
    """SlidingWindowLimiter's interface, shared by every instance."""

    def __init__(self, redis, window_seconds: float = 60.0) -> None:
        self._redis = redis
        self._window_ms = int(window_seconds * 1000)
        self._acquire = redis.register_script(_ACQUIRE)
        self._record = redis.register_script(_RECORD)

    def acquire(self, key: str, rpm: Optional[int], tpm: Optional[int]) -> Optional[Tuple[str, float]]:
        if rpm is None and tpm is None:
            return None
        try:
            verdict, retry_ms = self._acquire(
                keys=[f"{PREFIX}rl:req:{key}", f"{PREFIX}rl:tok:{key}"],
                args=[self._window_ms, -1 if rpm is None else rpm, -1 if tpm is None else tpm, uuid.uuid4().hex],
            )
        except Exception:  # noqa: BLE001 - fail open
            logger.warning("redis unavailable: rate limit not enforced for %s", key, exc_info=True)
            return None
        return (_REASONS[int(verdict)], max(0.0, int(retry_ms) / 1000)) if int(verdict) else None

    def record_tokens(self, key: str, count: int) -> None:
        if not count:
            return
        try:
            self._record(keys=[f"{PREFIX}rl:tok:{key}"], args=[f"{count}:{uuid.uuid4().hex}", self._window_ms])
        except Exception:  # noqa: BLE001
            logger.warning("redis unavailable: tokens not recorded for %s", key, exc_info=True)


class RedisResponseCache:
    """ResponseCache's interface for chat RouteResults, shared by every
    instance. Redis evicts by TTL (and its own maxmemory policy, if set)."""

    def __init__(self, redis, ttl_seconds: float, max_bytes: int = 16_777_216) -> None:
        self._redis = redis
        self._ttl = ttl_seconds
        self._max_bytes = max_bytes

    make_key = staticmethod(ResponseCache.make_key)

    def get(self, key: str) -> Optional[Any]:
        try:
            raw = self._redis.get(PREFIX + "cache:" + key)
        except Exception:  # noqa: BLE001 - fail open: a miss
            logger.warning("redis unavailable: cache lookup skipped", exc_info=True)
            return None
        if raw is None:
            return None
        data = json.loads(raw)
        data["usage"] = TokenUsage(*data["usage"]) if data["usage"] else None
        return RouteResult(**data)

    def set(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
        payload = json.dumps(value._asdict(), ensure_ascii=False)
        if len(payload.encode()) > self._max_bytes:
            return
        try:
            self._redis.set(PREFIX + "cache:" + key, payload, px=int((ttl_seconds or self._ttl) * 1000))
        except Exception:  # noqa: BLE001
            logger.warning("redis unavailable: response not cached", exc_info=True)

    def clear(self) -> int:
        keys = list(self._redis.scan_iter(match=PREFIX + "cache:*", count=1000))
        return self._redis.delete(*keys) if keys else 0
