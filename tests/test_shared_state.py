"""Several gateway instances sharing state through Redis (fakeredis here):
rate limits, response cache, budget reservations, key revocation, config
reloads; and failing open when Redis is down."""
import asyncio
import os
from datetime import datetime, timezone

import fakeredis
import pytest

from app import auth
from app.auth import Principal, hash_key
from app.database.models import ApiKey, Budget
from app.gateway import runtime
from app.gateway.budgets import BudgetExceeded, BudgetTracker, RedisReservations
from app.gateway.shared import RedisLimiter, RedisResponseCache, connect
from app.providers.base import TokenUsage
from app.router.model_router import RouteResult


# REDIS_TEST_URL=redis://... runs these against a real Redis (DISPOSABLE:
# the database is flushed); otherwise an in-memory fake.
REAL_REDIS = os.environ.get("REDIS_TEST_URL")


@pytest.fixture
def server():
    if REAL_REDIS:
        connect(REAL_REDIS).flushdb()
        return REAL_REDIS
    return fakeredis.FakeServer()


def instance(server):
    """A Redis client as one gateway instance would hold it."""
    return connect(server) if REAL_REDIS else fakeredis.FakeRedis(server=server)


def test_rate_limits_hold_across_instances(server):
    a, b = RedisLimiter(instance(server)), RedisLimiter(instance(server))
    assert a.acquire("key_1", rpm=2, tpm=None) is None
    assert b.acquire("key_1", rpm=2, tpm=None) is None
    reason, retry_after = a.acquire("key_1", rpm=2, tpm=None)
    assert reason == "requests_per_minute" and 0 < retry_after <= 60
    assert b.acquire("key_2", rpm=2, tpm=None) is None  # other keys unaffected

    a.record_tokens("key_3", 80)
    b.record_tokens("key_3", 30)
    assert a.acquire("key_3", rpm=None, tpm=100)[0] == "tokens_per_minute"


def test_response_cache_is_shared(server):
    a = RedisResponseCache(instance(server), ttl_seconds=60)
    b = RedisResponseCache(instance(server), ttl_seconds=60)
    answer = RouteResult("gemini", "flash", "hi", TokenUsage(1, 2, 3), deployment="gemini/flash")
    a.set("k", answer)
    assert b.get("k") == answer and b.get("missing") is None
    assert b.clear() == 1 and a.get("k") is None


def test_budget_reservations_are_shared(server, db_session):
    db_session.add(Budget(scope="org", target="*", period="daily", limit_usd=10))
    db_session.commit()
    principal = Principal("key_1", "k", "search", None, None, None, None)
    a = BudgetTracker(RedisReservations(instance(server)))
    b = BudgetTracker(RedisReservations(instance(server)))

    held = a.admit(db_session, principal, 6)
    with pytest.raises(BudgetExceeded):
        b.admit(db_session, principal, 6)  # instance a's request is still in flight
    held.release()
    b.admit(db_session, principal, 6).release()
    assert b.reservations.held("org:*:daily") == 0


def test_revocation_reaches_other_instances(server, db_session, monkeypatch):
    raw = "gw_live_test"
    db_session.add(ApiKey(name="k", project="p", key_hash=hash_key(raw), key_prefix="gw_live_",
                          revoked_at=datetime.now(timezone.utc)))
    db_session.commit()
    monkeypatch.setattr(auth, "get_redis", lambda: instance(server))
    # this instance validated the key before it was revoked elsewhere
    principal = Principal("key_x", "k", "p", None, None, None, None)
    auth._cache[hash_key(raw)] = (float("inf"), principal)
    assert auth._lookup(db_session, hash_key(raw)) == (principal, False)

    instance(server).set(f"{auth.REVOKED_PREFIX}{hash_key(raw)}", 1)  # forget_key on another instance
    assert auth._lookup(db_session, hash_key(raw)) == (None, True)


async def test_config_changes_reload_other_instances(server, monkeypatch):
    monkeypatch.setattr(runtime, "get_redis", lambda: instance(server))
    reloads = []
    monkeypatch.setattr(runtime, "reload", lambda db, services: reloads.append(services))
    follower = asyncio.create_task(runtime.follow("services", lambda: type("S", (), {"close": lambda s: None})(),
                                                  interval=0.01))
    await asyncio.sleep(0.05)
    assert reloads == []
    instance(server).incr(runtime.CONFIG_VERSION_KEY)  # announce() on another instance
    await asyncio.sleep(0.05)
    follower.cancel()
    assert reloads == ["services"]


def test_redis_outage_fails_open():
    down = connect("redis://127.0.0.1:1/0")
    assert RedisLimiter(down).acquire("key_1", rpm=1, tpm=None) is None
    cache = RedisResponseCache(down, ttl_seconds=60)
    cache.set("k", RouteResult("p", "m", "x"))
    assert cache.get("k") is None
    assert RedisReservations(down).reserve(["org:*:daily"], 2.0) == {"org:*:daily": 2.0}
    assert down.down_until > 0  # later calls skip Redis without waiting
