"""Budgets per team, user and organization: hard stop with in-flight
reservations, alerts at 80%/100%, admin API."""
import pytest

from app.auth import Principal
from app.database.models import Budget
from app.gateway import budgets
from app.gateway.budgets import BudgetExceeded, BudgetTracker
from app.gateway.config import PriceConfig
from app.providers.base import TokenUsage
from tests.conftest import build_services

ADMIN = {"Authorization": "Bearer admin-token"}
# $1 per input token, $2 per output token: a (1, 1) reply costs $3
PRICED = {"gemini/gemini-flash-latest": PriceConfig(input_per_million=1_000_000, output_per_million=2_000_000)}


@pytest.fixture
def auth(settings, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_token", "admin-token")


def _key(client, **fields):
    body = client.post("/admin/keys", json={"name": "k", "project": "search", **fields}, headers=ADMIN).json()
    return {"Authorization": f"Bearer {body['key']}"}


def _chat(client, headers, model="gemini"):
    return client.post("/v1/chat/completions", json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
                       headers=headers)


def test_team_budget_refuses_a_request_that_would_cross_it(client, auth, gateway, fake_providers):
    # output capped at 1 token: worst case = 1 input token ("hi") + 1 output = $3
    gateway["services"] = build_services(pricing=PRICED, default_max_output_tokens=1)
    fake_providers["gemini"].usage = TokenUsage(1, 1, 2)
    client.put("/admin/budgets", json={"scope": "team", "target": "search", "limit_usd": 10}, headers=ADMIN)
    headers = _key(client)

    assert [_chat(client, headers).status_code for _ in range(3)] == [200, 200, 200]  # $9 spent
    refused = _chat(client, headers)  # $9 + $3 worst case > $10, although $9 < $10
    assert refused.status_code == 429 and "team 'search' monthly budget exceeded" in refused.json()["detail"]
    other_team = client.post("/admin/keys", json={"name": "k", "project": "other"}, headers=ADMIN).json()["key"]
    assert _chat(client, {"Authorization": f"Bearer {other_team}"}).status_code == 200

    (status,) = client.get("/admin/budgets", headers=ADMIN).json()
    assert (status["spent_usd"], status["used_pct"], status["reserved_usd"]) == (9.0, 90.0, 0.0)
    alerts = client.get("/admin/budget-alerts", headers=ADMIN).json()
    assert [a["threshold_pct"] for a in alerts] == [100, 80]  # refused at 90%: the 100% alert still fires
    assert client.get("/admin/budgets", headers=ADMIN).json()[0]["limit_reached"] is True


def test_user_and_org_budgets(client, auth, gateway, fake_providers):
    gateway["services"] = build_services(pricing=PRICED)
    fake_providers["gemini"].usage = TokenUsage(1, 1, 2)
    client.put("/admin/budgets", json={"scope": "user", "target": "alice", "period": "daily", "limit_usd": 3},
               headers=ADMIN)
    alice, service = _key(client, user_id="alice"), _key(client)

    assert _chat(client, alice).status_code == 200
    assert _chat(client, alice).status_code == 429  # alice's $3 a day is used up
    assert _chat(client, service).status_code == 200  # service keys aren't alice

    client.put("/admin/budgets", json={"scope": "org", "limit_usd": 6}, headers=ADMIN)
    assert _chat(client, service).status_code == 429  # $6 spent company-wide
    assert "organization monthly" in _chat(client, service).json()["detail"]


def test_in_flight_reservations_count_against_the_budget(db_session):
    db_session.add(Budget(scope="team", target="search", period="monthly", limit_usd=10))
    db_session.commit()
    principal = Principal("key_1", "k", "search", None, None, None, None)
    tracker = BudgetTracker()

    first = tracker.admit(db_session, principal, 6)
    with pytest.raises(BudgetExceeded):
        tracker.admit(db_session, principal, 6)  # 6 in flight + 6 > 10
    assert tracker.reservations.held("team:search:monthly") == 6  # the refused one reserved nothing
    first.release()
    first.release()  # idempotent
    tracker.admit(db_session, principal, 6).release()
    assert tracker.reservations.held("team:search:monthly") == 0


def test_alerts_fire_once_per_threshold(client, auth, gateway, fake_providers, monkeypatch):
    sent = []
    monkeypatch.setattr(budgets, "send_webhook", sent.append)
    gateway["services"] = build_services(pricing=PRICED)
    fake_providers["gemini"].usage = TokenUsage(1, 1, 2)
    client.put("/admin/budgets", json={"scope": "team", "target": "search", "limit_usd": 7.5}, headers=ADMIN)
    headers = _key(client)

    codes = [_chat(client, headers).status_code for _ in range(5)]  # spend: 0, 3, 6 (80%), 9 (100%)
    assert codes == [200, 200, 200, 429, 429]
    assert [a["threshold_pct"] for a in sent] == [80, 100]
    assert "team 'search' monthly budget at 80%" in sent[0]["text"]
    alerts = client.get("/admin/budget-alerts", headers=ADMIN).json()
    assert [(a["threshold_pct"], a["spent_usd"]) for a in alerts] == [(100, 9.0), (80, 6.0)]


def test_worst_case_cost():
    targets = [("gemini", "gemini-flash-latest"), ("groq", "unpriced")]
    assert budgets.worst_case_cost(targets, PRICED, input_chars=7, max_output_tokens=2) == 3 + 4
    assert budgets.worst_case_cost([("groq", "x")], PRICED, 100, 100) == 0.0  # unknown price: nothing reserved
    assert budgets.period_end("monthly", budgets.period_start("monthly")).day == 1


def test_budgets_are_cached_and_survive_a_database_outage(db_session, monkeypatch):
    from sqlalchemy.exc import OperationalError

    now = [0.0]
    tracker = BudgetTracker(clock=lambda: now[0])
    db_session.add(Budget(scope="org", target="*", period="daily", limit_usd=10))
    db_session.commit()
    assert [b.limit_usd for b in tracker.budgets(db_session)] == [10]

    db_session.query(Budget).update({Budget.limit_usd: 20})
    db_session.commit()
    assert [b.limit_usd for b in tracker.budgets(db_session)] == [10]  # cached
    now[0] = 6
    assert [b.limit_usd for b in tracker.budgets(db_session)] == [20]  # refreshed after the TTL

    def database_down(*_args, **_kwargs):
        raise OperationalError("SELECT", {}, Exception("connection refused"))

    monkeypatch.setattr(db_session, "query", database_down)
    now[0] = 12
    assert [b.limit_usd for b in tracker.budgets(db_session)] == [20]  # last copy while it's down
    tracker.invalidate()
    with pytest.raises(OperationalError):
        tracker.budgets(db_session)  # nothing to fall back on
