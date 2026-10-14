"""Sliding-window rate limiting per API key: requests/minute and
tokens/minute.

Requests are counted when admitted. Tokens are only known after the
provider answers, so they are recorded afterwards: a key that is at its
TPM limit is rejected on its *next* request (one request can overshoot -
the standard trade-off without pre-estimating tokens).

ponytail: in-process state, so limits are per gateway instance. Behind a
load balancer with N instances the effective limit is up to N x the
configured one; move the windows to Redis (sorted sets) at that point.
"""
import time
from collections import defaultdict, deque
from functools import lru_cache
from typing import Callable, Deque, Dict, Optional, Tuple

_WINDOW_SECONDS = 60.0


class SlidingWindowLimiter:
    def __init__(self, window_seconds: float = _WINDOW_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._window = window_seconds
        self._clock = clock
        self._requests: Dict[str, Deque[float]] = defaultdict(deque)
        self._tokens: Dict[str, Deque[Tuple[float, int]]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> None:
        cutoff = now - self._window
        requests, tokens = self._requests[key], self._tokens[key]
        while requests and requests[0] <= cutoff:
            requests.popleft()
        while tokens and tokens[0][0] <= cutoff:
            tokens.popleft()

    def acquire(self, key: str, rpm: Optional[int], tpm: Optional[int]) -> Optional[Tuple[str, float]]:
        """Admit a request (counting it) or return (reason, retry_after_seconds)."""
        now = self._clock()
        self._prune(key, now)
        requests, tokens = self._requests[key], self._tokens[key]
        if rpm is not None and len(requests) >= rpm:
            return "requests_per_minute", requests[0] + self._window - now
        if tpm is not None and sum(n for _, n in tokens) >= tpm:
            return "tokens_per_minute", tokens[0][0] + self._window - now
        requests.append(now)
        return None

    def record_tokens(self, key: str, count: int) -> None:
        if count:
            self._tokens[key].append((self._clock(), count))


@lru_cache
def get_rate_limiter() -> SlidingWindowLimiter:
    return SlidingWindowLimiter()
