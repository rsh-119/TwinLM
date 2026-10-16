"""Semantic response cache: reuses an answer when a new prompt *means* the
same as a cached one ("opening hours?" ~ "when are you open?").

Entries live in a *scope*: a hash of everything except the last user
message (model, earlier messages, sampling params, response format,
project), which must match exactly. Within a scope the last user message
is compared by cosine similarity of its embedding. Only aliases listed in
`[cache.semantic] models` use it, after the exact-match cache missed.

ponytail: in-process, linear scan over a scope's entries (a numpy matrix
product, well under a millisecond for the default 1000 entries). Each
gateway instance keeps its own; a vector database is the upgrade path for
large caches or sharing across instances.
"""
import time
from collections import OrderedDict
from itertools import count
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np


class SemanticCache:
    def __init__(self, min_similarity: float, max_entries: int, ttl_seconds: float,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._min = min_similarity
        self._max = max_entries
        self._ttl = ttl_seconds
        self._clock = clock
        self._ids = count()
        # id -> (scope, unit vector, value, expires at), oldest first
        self._entries: "OrderedDict[int, Tuple[str, np.ndarray, Any, float]]" = OrderedDict()
        self._scopes: Dict[str, List[int]] = {}

    @staticmethod
    def _unit(vector: List[float]) -> Optional[np.ndarray]:
        array = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(array))
        return array / norm if norm else None

    def get(self, scope: str, vector: List[float]) -> Optional[Tuple[Any, float]]:
        """(cached value, similarity) of the closest live entry at or above
        the threshold, else None."""
        unit = self._unit(vector)
        now = self._clock()
        ids = [i for i in self._scopes.get(scope, []) if self._entries[i][3] > now]
        if unit is None or not ids:
            return None
        matrix = np.stack([self._entries[i][1] for i in ids])
        if matrix.shape[1] != unit.shape[0]:
            return None  # embedding model changed
        similarities = matrix @ unit
        best = int(np.argmax(similarities))
        similarity = float(similarities[best])
        if similarity < self._min:
            return None
        self._entries.move_to_end(ids[best])
        return self._entries[ids[best]][2], similarity

    def set(self, scope: str, vector: List[float], value: Any, ttl_seconds: Optional[float] = None) -> None:
        unit = self._unit(vector)
        if unit is None:
            return
        entry_id = next(self._ids)
        self._entries[entry_id] = (scope, unit, value, self._clock() + (ttl_seconds or self._ttl))
        self._scopes.setdefault(scope, []).append(entry_id)
        now = self._clock()
        while len(self._entries) > self._max:
            self._remove(next(iter(self._entries)))
        for stale in [i for i in self._scopes.get(scope, []) if self._entries[i][3] <= now]:
            self._remove(stale)

    def _remove(self, entry_id: int) -> None:
        scope = self._entries.pop(entry_id)[0]
        ids = self._scopes[scope]
        ids.remove(entry_id)
        if not ids:
            del self._scopes[scope]

    def clear(self) -> int:
        removed = len(self._entries)
        self._entries.clear()
        self._scopes.clear()
        return removed

    def __len__(self) -> int:
        return len(self._entries)

