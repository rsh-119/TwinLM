"""Per-provider health: circuit breaker state plus the live stats routing
and the admin API read (latency, in-flight load, error counts).

ponytail: in-process state - each gateway instance keeps its own breakers.
Fine for one instance; with several, move state to Redis so instances
agree on when a provider is down.
"""
import time
from enum import Enum
from typing import Callable, Optional

_EWMA_ALPHA = 0.2


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class ProviderHealth:
    """CLOSED --N consecutive failures--> OPEN --cooldown--> HALF_OPEN
    --one trial succeeds--> CLOSED (fails --> OPEN again).

    Only availability failures (timeouts, 429/5xx, connection errors)
    count. A client error means the provider answered, so it is neutral.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._clock = clock

        self.state = BreakerState.CLOSED
        self.consecutive_failures = 0
        self._opened_at = 0.0
        self._trial_in_flight = False

        self.total_requests = 0
        self.total_failures = 0
        self.in_flight = 0
        self.latency_ewma: Optional[float] = None
        self.last_success_at: Optional[float] = None  # wall clock, for display
        self.last_error: Optional[str] = None

    def allow_request(self) -> bool:
        """Whether to send a request now. In HALF_OPEN, admits exactly one
        trial request at a time."""
        if self.state is BreakerState.OPEN and self._clock() - self._opened_at >= self._cooldown:
            self.state = BreakerState.HALF_OPEN
            self._trial_in_flight = False
        if self.state is BreakerState.OPEN:
            return False
        if self.state is BreakerState.HALF_OPEN:
            if self._trial_in_flight:
                return False
            self._trial_in_flight = True
        return True

    def record_success(self, latency_seconds: float) -> None:
        self.total_requests += 1
        self.state = BreakerState.CLOSED
        self.consecutive_failures = 0
        self._trial_in_flight = False
        self.last_success_at = time.time()
        self.latency_ewma = (
            latency_seconds
            if self.latency_ewma is None
            else _EWMA_ALPHA * latency_seconds + (1 - _EWMA_ALPHA) * self.latency_ewma
        )

    def record_failure(self, error: str) -> None:
        self.total_requests += 1
        self.total_failures += 1
        self.consecutive_failures += 1
        self.last_error = error[:300]
        if self.state is BreakerState.HALF_OPEN or self.consecutive_failures >= self._threshold:
            self.state = BreakerState.OPEN
            self._opened_at = self._clock()
        self._trial_in_flight = False

    def record_neutral(self) -> None:
        """The provider answered with a client error - says nothing about
        its availability, but frees a half-open trial slot."""
        self.total_requests += 1
        self._trial_in_flight = False

    @property
    def available(self) -> bool:
        """Read-only view for routing order and health reporting (unlike
        `allow_request`, never changes state)."""
        if self.state is BreakerState.OPEN:
            return self._clock() - self._opened_at >= self._cooldown
        return True

    def snapshot(self) -> dict:
        return {
            "provider": self.name,
            "state": self.state.value,
            "available": self.available,
            "consecutive_failures": self.consecutive_failures,
            "total_requests": self.total_requests,
            "total_failures": self.total_failures,
            "error_rate": round(self.total_failures / self.total_requests, 4) if self.total_requests else 0.0,
            "in_flight": self.in_flight,
            "latency_ewma_ms": round(self.latency_ewma * 1000, 1) if self.latency_ewma is not None else None,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
        }
