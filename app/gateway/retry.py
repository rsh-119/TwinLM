"""Bounded retry with exponential backoff and full jitter."""
import random
from typing import NamedTuple


class RetryPolicy(NamedTuple):
    max_retries: int = 0
    backoff_base_seconds: float = 0.2
    backoff_max_seconds: float = 2.0

    def delay(self, retry_number: int) -> float:
        """Sleep before retry #`retry_number` (0-based): uniform in
        [0, min(max, base * 2^n)] - "full jitter", so clients that failed
        together don't retry together."""
        cap = min(self.backoff_max_seconds, self.backoff_base_seconds * (2 ** retry_number))
        return random.uniform(0, cap)
