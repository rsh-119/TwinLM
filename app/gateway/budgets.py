"""Spending budgets: per API key (daily, `daily_budget_usd`), per team,
per user and for the whole organization (daily or monthly, `budgets`
table), with alerts at 80% and 100%.

Admission counts settled spend (request_logs), spend still in the local
outbox, and in-flight *reservations*: before a request goes out, its
worst-case cost (input estimate + output cap, at the priciest model the
alias may fall back to) is reserved and released once its real cost is
queued. A request that would push any budget past its limit is refused,
so budgets are hard as long as the output is capped and prices are known.
Unpriced models reserve nothing (their cost is unknown) and only stop the
next request.

Reservations are in-process, or in Redis when REDIS_URL is set so several
gateway instances share them (app.gateway.shared)."""
import logging
import math
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence, Set, Tuple

import httpx
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth import Principal
from app.config import get_settings
from app.database import repository
from app.database.models import Budget, BudgetAlert
from app.gateway.config import PriceConfig
from app.gateway.pricing import price_key
from app.observability import BUDGET_ALERTS

logger = logging.getLogger("twinlm.budgets")

ALERT_THRESHOLDS = (80, 100)  # percent of the limit
_RESERVATION_TTL_SECONDS = 900  # Redis: a crashed instance's reservations expire
# Budget edits apply at once on the instance that made them, within this on others.
_BUDGETS_TTL_SECONDS = 5.0


def period_start(period: str, now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.replace(day=1) if period == "monthly" else start


def period_end(period: str, start: datetime) -> datetime:
    if period == "daily":
        return start + timedelta(days=1)
    return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)


@dataclass(frozen=True)
class Limit:
    """One budget that applies to a request."""

    key: str  # reservation key, e.g. "team:search:monthly"
    label: str  # for errors and alerts, e.g. "team 'search' monthly"
    limit_usd: float
    since: datetime
    filters: Dict[str, Optional[str]]  # sum_cost_since filters
    budget_id: Optional[str] = None  # None = an API key's daily_budget_usd


def describe(budget: Budget) -> str:
    who = "organization" if budget.scope == "org" else f"{budget.scope} {budget.target!r}"
    return f"{who} {budget.period}"


def limit_for(budget: Budget, now: Optional[datetime] = None) -> Limit:
    filters = {"org": {}, "team": {"project": budget.target}, "user": {"user_id": budget.target}}[budget.scope]
    return Limit(f"{budget.scope}:{budget.target}:{budget.period}", describe(budget), budget.limit_usd,
                 period_start(budget.period, now), filters, budget.id)


class BudgetRow(NamedTuple):
    """A budgets row detached from any session (the tracker caches these)."""

    id: str
    scope: str
    target: str
    period: str
    limit_usd: float


def applicable(principal: Principal, budgets: Iterable[BudgetRow]) -> List[Limit]:
    limits = []
    if principal.daily_budget_usd is not None:
        limits.append(Limit(f"key:{principal.key_id}:daily", "API key daily", principal.daily_budget_usd,
                            period_start("daily"), {"api_key_id": principal.key_id}))
    for budget in budgets:
        if (budget.scope == "org" or (budget.scope == "team" and budget.target == principal.project)
                or (budget.scope == "user" and principal.user_id and budget.target == principal.user_id)):
            limits.append(limit_for(budget))
    return limits


def spent(db: Session, limit: Limit, writer=None) -> float:
    """Settled plus still-queued known cost in the limit's period."""
    settled = repository.sum_cost_since(db, limit.since, **limit.filters)
    pending = writer.pending_cost_since(limit.since, **limit.filters) if writer is not None else 0.0
    return settled + pending


def worst_case_cost(
    targets: Iterable[Tuple[str, str]], pricing: Dict[str, PriceConfig], input_chars: int,
    max_output_tokens: Optional[int],
) -> float:
    """Upper estimate of one request's cost over the targets it may be sent
    to; 0 when none is priced. Input tokens ~ chars/3 (most tokenizers
    average about 4 characters per token for English)."""
    prices = [pricing[price_key(p, m)] for p, m in targets if price_key(p, m) in pricing]
    if not prices:
        return 0.0
    input_tokens = math.ceil(input_chars / 3)
    return (input_tokens * max(p.input_per_million for p in prices)
            + (max_output_tokens or 0) * max(p.output_per_million for p in prices)) / 1_000_000


def status(db: Session, budget: Budget, tracker: "BudgetTracker", writer=None) -> dict:
    """A budget with its spend in the current period, for the admin API."""
    limit = limit_for(budget)
    spend = spent(db, limit, writer)
    return {"id": budget.id, "scope": budget.scope, "target": budget.target, "period": budget.period,
            "label": limit.label, "limit_usd": budget.limit_usd, "spent_usd": round(spend, 6),
            "reserved_usd": round(tracker.reservations.held(limit.key), 6),
            "used_pct": round(100 * spend / budget.limit_usd, 1),
            # requests were refused this period (possibly before spend hit 100%)
            "limit_reached": db.query(BudgetAlert).filter(
                BudgetAlert.budget_id == budget.id, BudgetAlert.threshold == 100,
                BudgetAlert.period_start == limit.since.replace(tzinfo=None)).first() is not None,
            "period_start": limit.since.isoformat(), "resets_at": period_end(budget.period, limit.since).isoformat()}


class BudgetExceeded(Exception):
    def __init__(self, limit: Limit):
        super().__init__(f"{limit.label} budget exceeded")
        self.limit = limit


class Reservation:
    def __init__(self, store, keys: Sequence[str], amount: float):
        self._store, self.keys, self.amount = store, list(keys), amount
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._store.release(self.keys, self.amount)


class LocalReservations:
    """In-flight reserved USD per budget key, for one gateway instance."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: Dict[str, float] = defaultdict(float)

    def reserve(self, keys: Sequence[str], amount: float) -> Dict[str, float]:
        """Add `amount` to every key; returns each key's total held now."""
        with self._lock:
            for key in keys:
                self._held[key] += amount
            return {key: self._held[key] for key in keys}

    def release(self, keys: Sequence[str], amount: float) -> None:
        with self._lock:
            for key in keys:
                self._held[key] -= amount
                if self._held[key] <= 1e-12:
                    del self._held[key]

    def held(self, key: str) -> float:
        with self._lock:
            return self._held.get(key, 0.0)


class RedisReservations:
    """Same, shared by every instance through Redis. A crashed instance's
    reservations expire after _RESERVATION_TTL_SECONDS."""

    _ADD = """
    local totals = {}
    for i, key in ipairs(KEYS) do
      totals[i] = redis.call('INCRBYFLOAT', key, ARGV[1])
      redis.call('EXPIRE', key, ARGV[2])
    end
    return totals
    """

    def __init__(self, redis) -> None:
        self._add = redis.register_script(self._ADD)
        self._redis = redis

    @staticmethod
    def _names(keys: Sequence[str]) -> List[str]:
        return [f"twinlm:budget:held:{key}" for key in keys]

    def reserve(self, keys: Sequence[str], amount: float) -> Dict[str, float]:
        try:
            totals = self._add(keys=self._names(keys), args=[repr(amount), _RESERVATION_TTL_SECONDS])
        except Exception:  # noqa: BLE001 - fail open: settled spend is still checked
            logger.warning("redis unavailable: other requests' budget reservations not counted", exc_info=True)
            return {key: amount for key in keys}
        return {key: float(total) for key, total in zip(keys, totals)}

    def release(self, keys: Sequence[str], amount: float) -> None:
        try:
            self._add(keys=self._names(keys), args=[repr(-amount), _RESERVATION_TTL_SECONDS])
        except Exception:  # noqa: BLE001 - the keys' TTL clears it eventually
            logger.warning("redis unavailable: budget reservation not released", exc_info=True)

    def held(self, key: str) -> float:
        try:
            return max(0.0, float(self._redis.get(self._names([key])[0]) or 0.0))
        except Exception:  # noqa: BLE001
            return 0.0


class BudgetTracker:
    def __init__(self, reservations=None, clock=time.monotonic) -> None:
        self.reservations = reservations or LocalReservations()
        self._alerted: Set[Tuple[str, datetime, int]] = set()
        self._clock = clock
        self._budgets: Optional[List[BudgetRow]] = None
        self._loaded_at = 0.0

    def budgets(self, db: Session) -> List[BudgetRow]:
        """The budgets table, re-read at most every _BUDGETS_TTL_SECONDS, so a
        request no budget applies to needs no database (stateless chat keeps
        working through a database outage). While the database is down the
        last copy is used."""
        now = self._clock()
        if self._budgets is None or now - self._loaded_at >= _BUDGETS_TTL_SECONDS:
            try:
                self._budgets = [BudgetRow(b.id, b.scope, b.target, b.period, b.limit_usd)
                                 for b in db.query(Budget).all()]
                self._loaded_at = now
            except SQLAlchemyError:
                if self._budgets is None:
                    raise
                logger.warning("database unavailable; using the last loaded budgets")
        return self._budgets

    def invalidate(self) -> None:
        """Budgets changed (admin API): re-read them on the next request."""
        self._budgets = None

    def admit(self, db: Session, principal: Principal, estimate: float, writer=None) -> Optional[Reservation]:
        """Reserve `estimate` against every budget that applies, or raise
        BudgetExceeded (nothing stays reserved). Reserving first and then
        checking means two concurrent requests can both be refused near the
        limit, never both admitted past it."""
        limits = applicable(principal, self.budgets(db))
        if not limits:
            return None
        totals = self.reservations.reserve([l.key for l in limits], estimate)
        reservation = Reservation(self.reservations, [l.key for l in limits], estimate)
        try:
            for limit in limits:
                spend = spent(db, limit, writer)
                others = totals[limit.key] - estimate  # other requests' reservations
                refused = spend + others >= limit.limit_usd or spend + totals[limit.key] > limit.limit_usd
                self.maybe_alert(db, limit, spend, refused)
                if refused:
                    raise BudgetExceeded(limit)
        except BaseException:
            reservation.release()
            raise
        return reservation

    def maybe_alert(self, db: Session, limit: Limit, spend: float, refused: bool = False) -> None:
        """Record and send each threshold once per budget period. The 100%
        alert also fires on the first refused request: a request's worst
        case can be refused before spend itself reaches the limit."""
        if limit.budget_id is None or limit.limit_usd <= 0:
            return
        start = limit.since.replace(tzinfo=None)
        for threshold in ALERT_THRESHOLDS:
            marker = (limit.budget_id, start, threshold)
            reached = spend >= limit.limit_usd * threshold / 100 or (refused and threshold == 100)
            if not reached or marker in self._alerted:
                continue
            self._alerted.add(marker)
            db.add(BudgetAlert(budget_id=limit.budget_id, period_start=start, threshold=threshold,
                               spent_usd=round(spend, 6), limit_usd=limit.limit_usd))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()  # already sent (another instance, or before a restart)
                continue
            BUDGET_ALERTS.labels(str(threshold)).inc()
            message = (f"TwinLM: {limit.label} budget reached - requests are being refused "
                       f"(${spend:.2f} spent of ${limit.limit_usd:.2f})" if threshold == 100 else
                       f"TwinLM: {limit.label} budget at {threshold}% (${spend:.2f} of ${limit.limit_usd:.2f})")
            logger.warning(message)
            send_webhook({"text": message, "budget_id": limit.budget_id, "budget": limit.label,
                          "threshold_pct": threshold, "spent_usd": round(spend, 6), "limit_usd": limit.limit_usd})


def send_webhook(payload: dict) -> None:
    """POST to BUDGET_ALERT_WEBHOOK_URL in the background (Slack-style
    `text`). ponytail: fire-and-forget, no retry; the alert row and the log
    line remain if delivery fails."""
    url = get_settings().budget_alert_webhook_url
    if not url:
        return

    def post() -> None:
        try:
            httpx.post(url, json=payload, timeout=5).raise_for_status()
        except Exception:  # noqa: BLE001
            logger.exception("budget alert webhook failed")

    threading.Thread(target=post, name=f"budget-alert-{uuid.uuid4().hex[:6]}", daemon=True).start()
