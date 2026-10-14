"""Per-request accounting: one `request_logs` row, Prometheus metrics, and
the token counts that feed per-key TPM limits - all in `finish`, so every
code path (success, error, cache hit, client disconnect) records the same
way.

Rows are written either synchronously on the request's session or first
committed to a local SQLite outbox. A background thread copies outbox rows
to the main database; failed batches remain available for replay.
"""
import logging
import json
import sqlite3
import threading
import time
import tempfile
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.exc import DataError, IntegrityError

from app.auth import Principal
from app.database import repository
from app.database.models import RequestLog
from app.gateway.config import PriceConfig
from app.gateway.pricing import price_key, request_cost
from app.gateway.rate_limit import SlidingWindowLimiter
from app.observability import (
    ACTIVE_REQUESTS, COST, REQUEST_LATENCY, REQUESTS, TOKENS, UNPRICED_CALLS, USAGE_BACKLOG, USAGE_DEADLETTER,
    USAGE_WRITE_FAILURES,
)
from app.providers.base import TokenUsage

logger = logging.getLogger("twinlm.usage")


@dataclass
class UsageRecord:
    request_id: str
    endpoint: str
    requested_model: str
    stream: bool = False
    principal: Optional[Principal] = None
    conversation_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    usage: Optional[TokenUsage] = None
    billed_calls: List[tuple[str, str, Optional[TokenUsage]]] = field(default_factory=list)
    status: str = "success"
    http_status: int = 200
    error: Optional[str] = None
    fallback: bool = False
    cache_hit: bool = False
    deployment: Optional[str] = None
    ttft: Optional[float] = None
    started: float = field(default_factory=time.monotonic)
    finished: bool = False

    def __post_init__(self) -> None:
        ACTIVE_REQUESTS.inc()

    def fail(self, http_status: int, error: str, status: str = "error") -> None:
        self.status, self.http_status, self.error = status, http_status, error[:500]

    def bill(self, provider: str, model: str, usage: Optional[TokenUsage]) -> None:
        self.billed_calls.append((provider, model, usage))

    def served_by(self, result) -> None:
        """Attribute a routed call (RouteResult/EmbedResult) to this request and bill it."""
        self.provider, self.model, self.usage = result.provider, result.model, result.usage
        self.fallback, self.deployment = result.fallback, result.deployment
        self.bill(result.provider, result.model, result.usage)


def finish(
    db: Session,
    record: UsageRecord,
    pricing: Dict[str, PriceConfig],
    limiter: SlidingWindowLimiter,
    writer: Optional["UsageWriter"] = None,
) -> None:
    """Idempotent; never raises - accounting must not break a response."""
    if record.finished:
        return
    record.finished = True
    ACTIVE_REQUESTS.dec()

    latency = time.monotonic() - record.started
    provider, model, usage = record.provider, record.model, record.usage
    if record.cache_hit:
        cost: Optional[float] = 0.0
    elif record.billed_calls:
        costs = [request_cost(pricing, billed_provider, billed_model, billed_usage)
                 for billed_provider, billed_model, billed_usage in record.billed_calls]
        cost = sum(costs) if all(value is not None for value in costs) else None
    elif provider and model:
        cost = request_cost(pricing, provider, model, usage)
    else:
        cost = None

    metric_model = model if provider and model and price_key(provider, model) in pricing else "unpriced"
    REQUESTS.labels(record.endpoint, provider or "none", metric_model, record.status).inc()
    REQUEST_LATENCY.labels(record.endpoint, provider or "none").observe(latency)
    billed_calls = record.billed_calls or ([(provider, model, usage)] if provider and model else [])
    if not record.cache_hit:
        for billed_provider, billed_model, billed_usage in billed_calls:
            if request_cost(pricing, billed_provider, billed_model, billed_usage) is None:
                UNPRICED_CALLS.labels(billed_provider).inc()
            if billed_usage is None:
                continue
            billed_metric_model = billed_model if price_key(billed_provider, billed_model) in pricing else "unpriced"
            TOKENS.labels(billed_provider, billed_metric_model, "prompt").inc(billed_usage.prompt_tokens)
            TOKENS.labels(billed_provider, billed_metric_model, "completion").inc(billed_usage.completion_tokens)
            billed_cost = request_cost(pricing, billed_provider, billed_model, billed_usage)
            if billed_cost:
                COST.labels(billed_provider, billed_metric_model).inc(billed_cost)
    if usage and not record.cache_hit:
        if record.principal is not None:
            limiter.record_tokens(record.principal.key_id, usage.total_tokens)

    principal = record.principal
    row = dict(
        id=record.request_id,
        created_at=datetime.now(timezone.utc),
        endpoint=record.endpoint,
        api_key_id=principal.key_id if principal else None,
        project=principal.project if principal else None,
        conversation_id=record.conversation_id,
        requested_model=record.requested_model,
        provider=provider,
        model=model,
        deployment=record.deployment,
        stream=record.stream,
        status=record.status,
        http_status=record.http_status,
        error=record.error,
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
        total_tokens=usage.total_tokens if usage else None,
        cost_usd=cost,
        latency_ms=int(latency * 1000),
        ttft_ms=int(record.ttft * 1000) if record.ttft is not None else None,
        fallback_used=record.fallback,
        cache_hit=record.cache_hit,
    )
    if writer is not None:
        if writer.submit(row):
            return
    try:
        repository.add_request_log(db, **row)
    except Exception:  # noqa: BLE001
        logger.exception("failed to record usage for request_id=%s", record.request_id)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


class UsageWriter:
    """Durable local outbox for batched request-log writes.

    A failed database batch remains in the outbox and is retried. Replays
    check existing IDs so a crash after the database commit is harmless.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        max_batch: int = 500,
        flush_interval_seconds: float = 0.5,
        max_queue: int = 100_000,
        spool_path: Optional[str] = None,
    ) -> None:
        self._session_factory = session_factory
        self._max_batch = max_batch
        self._interval = flush_interval_seconds
        self._max_queue = max_queue
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._temporary = tempfile.TemporaryDirectory() if spool_path is None else None
        path = Path(spool_path) if spool_path is not None else Path(self._temporary.name) / "usage.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS pending ("
            "id TEXT PRIMARY KEY, payload TEXT NOT NULL, api_key_id TEXT, created_at TEXT, cost_usd REAL)"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS ix_pending_key_time ON pending (api_key_id, created_at)")
        self._conn.execute("CREATE TABLE IF NOT EXISTS deadletter (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        self._conn.commit()
        self.dropped = 0
        with self._lock:
            USAGE_BACKLOG.set(self._count_locked())
            USAGE_DEADLETTER.set(self._deadletter_count_locked())
        self._ensure_started()

    def submit(self, row: dict) -> bool:
        """Commit to the outbox before returning; caller falls back on failure."""
        try:
            with self._lock:
                if self._count_locked() >= self._max_queue:
                    raise RuntimeError("usage outbox capacity reached")
                payload = json.dumps(row, default=lambda value: value.isoformat() if isinstance(value, datetime) else value)
                self._conn.execute(
                    "INSERT OR IGNORE INTO pending (id, payload, api_key_id, created_at, cost_usd) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (row["id"], payload, row.get("api_key_id"),
                     row["created_at"].isoformat() if row.get("created_at") else None,
                     row.get("cost_usd")),
                )
                self._conn.commit()
                USAGE_BACKLOG.set(self._count_locked())
            self._wake.set()
            return True
        except Exception:  # noqa: BLE001 - caller can fall back to the main DB
            self.dropped += 1
            logger.exception("failed to spool usage row id=%s", row.get("id"))
            return False

    def pending_cost_since(self, api_key_id: str, since: datetime) -> float:
        with self._lock:
            return self._conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM pending WHERE api_key_id = ? AND created_at >= ?",
                (api_key_id, since.isoformat()),
            ).fetchone()[0]

    def flush(self, timeout: float = 10.0) -> bool:
        """Wait for the outbox to drain; return False if DB remains unavailable."""
        deadline = time.monotonic() + timeout
        self._wake.set()
        while time.monotonic() < deadline:
            with self._lock:
                if self._count_locked() == 0:
                    return True
            time.sleep(min(self._interval, 0.05))
        return False

    def close(self) -> None:
        if self._thread is not None:
            self._stop.set()
            self._wake.set()
            self._thread.join(timeout=10)
            self._thread = None
        with self._lock:
            self._conn.close()
        if self._temporary is not None:
            self._temporary.cleanup()

    def _ensure_started(self) -> None:
        if self._thread is None:
            with self._lock:
                if self._thread is None:
                    self._thread = threading.Thread(target=self._run, name="usage-writer", daemon=True)
                    self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self._interval)
            self._wake.clear()
            while not self._stop.is_set():
                with self._lock:
                    batch = self._conn.execute(
                        "SELECT id, payload FROM pending ORDER BY rowid LIMIT ?", (self._max_batch,)
                    ).fetchall()
                if not batch:
                    break
                if not self._write([self._decode(payload) for _, payload in batch]):
                    break
                with self._lock:
                    self._conn.executemany("DELETE FROM pending WHERE id = ?", [(key,) for key, _ in batch])
                    self._conn.commit()
                    USAGE_BACKLOG.set(self._count_locked())

    def _count_locked(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM pending").fetchone()[0]

    def _deadletter_count_locked(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM deadletter").fetchone()[0]

    def deadletter_count(self) -> int:
        with self._lock:
            return self._deadletter_count_locked()

    @staticmethod
    def _decode(payload: str) -> dict:
        row = json.loads(payload)
        if row.get("created_at"):
            row["created_at"] = datetime.fromisoformat(row["created_at"])
        return row

    def _write(self, batch: List[dict]) -> bool:
        try:
            with self._session_factory() as session:
                existing = {
                    key for (key,) in session.query(RequestLog.id)
                    .filter(RequestLog.id.in_([row["id"] for row in batch])).all()
                }
                missing = [row for row in batch if row["id"] not in existing]
                if missing:
                    session.bulk_insert_mappings(RequestLog, missing)
                session.commit()
            return True
        except (IntegrityError, DataError):
            if len(batch) > 1:
                midpoint = len(batch) // 2
                return self._write(batch[:midpoint]) and self._write(batch[midpoint:])
            row = batch[0]
            with self._lock:
                self._conn.execute(
                    "INSERT OR IGNORE INTO deadletter (id, payload) VALUES (?, ?)",
                    (row["id"], json.dumps(row, default=lambda value: value.isoformat() if isinstance(value, datetime) else value)),
                )
                self._conn.commit()
                USAGE_DEADLETTER.set(self._deadletter_count_locked())
            logger.error("usage row retained in deadletter after data error: id=%s", row["id"])
            USAGE_WRITE_FAILURES.inc()
            return True
        except Exception:  # noqa: BLE001
            USAGE_WRITE_FAILURES.inc()
            logger.exception("failed to write %d request log rows", len(batch))
            return False
