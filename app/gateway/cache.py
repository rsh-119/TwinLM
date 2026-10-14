"""Exact-match response cache for stateless, non-streaming chat requests.

The key is a SHA-256 over the normalized request (model, messages,
sampling params, response format) plus the caller's project, so cached
answers never cross projects. What gets cached is decided by the caller
(see app/api/chat.py): never stateful turns, streams, or requests the
guardrails flag as containing secrets/PII.

ponytail: in-process LRU, so each gateway instance has its own cache and
it is lost on restart. Swap for Redis (same get/set/clear surface) when
running more than one instance.
"""
import hashlib
import json
import time
from collections import OrderedDict
from functools import lru_cache
from typing import Any, Callable, Optional, Tuple

from app.gateway.config import get_gateway_config


class ResponseCache:
    def __init__(
        self, ttl_seconds: float, max_entries: int,
        clock: Callable[[], float] = time.monotonic, max_bytes: int = 16_777_216,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._max_bytes = max_bytes
        self._bytes = 0
        self._clock = clock
        self._entries: "OrderedDict[str, Tuple[float, Any, int]]" = OrderedDict()

    @staticmethod
    def make_key(**parts: Any) -> str:
        canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value, size = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            self._bytes -= size
            return None
        self._entries.move_to_end(key)
        return value

    def set(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
        size = len(json.dumps(value, default=str, ensure_ascii=False).encode("utf-8"))
        if size > self._max_bytes:
            return
        old = self._entries.get(key)
        if old is not None:
            self._bytes -= old[2]
        self._entries[key] = (self._clock() + (ttl_seconds or self._ttl), value, size)
        self._bytes += size
        self._entries.move_to_end(key)
        while len(self._entries) > self._max or self._bytes > self._max_bytes:
            _, (_, _, removed_size) = self._entries.popitem(last=False)
            self._bytes -= removed_size

    def clear(self) -> int:
        count = len(self._entries)
        self._entries.clear()
        self._bytes = 0
        return count

    def __len__(self) -> int:
        return len(self._entries)


@lru_cache
def get_response_cache() -> ResponseCache:
    config = get_gateway_config().cache
    return ResponseCache(config.ttl_seconds, config.max_entries, max_bytes=config.max_bytes)
