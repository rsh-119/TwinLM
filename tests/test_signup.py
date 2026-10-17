"""Hosted beta: self-service sign-up. Each sign-up gets their own team (as
its admin) with a monthly budget; the beta's limits can't be lifted by
team admins."""
import pytest

from app.gateway.config import PriceConfig
from app.gateway.rate_limit import get_rate_limiter
from app.providers.base import TokenUsage
from tests.conftest import build_services
from tests.test_teams import bearer, new_user, sign_in


@pytest.fixture(autouse=True)
def admin_token(settings, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "admin-token")
    get_rate_limiter.cache_clear()
    yield
    get_rate_limiter.cache_clear()


@pytest.fixture
def beta(client, settings, monkeypatch, gateway, fake_providers):
    """Sign-up on, auth on, the operator's admin account, beta limits."""
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "signup_enabled", True)
    monkeypatch.setattr(settings, "signup_max_accounts", 3)
    monkeypatch.setattr(settings, "signup_team_budget_usd", 5.0)
    gateway["services"] = build_services(
        rate_limits={"default_rpm": 20, "default_tpm": 40000},
        default_max_output_tokens=1,  # the beta caps output: budgets reserve the worst case
        pricing={"gemini/gemini-flash-latest": PriceConfig(input_per_million=1_000_000, output_per_million=2_000_000)},
    )
    fake_providers["gemini"].usage = TokenUsage(1, 1, 2)  # $3 a request at these prices
    admin = new_user(client, "op@twinlm.io", role="admin")
    return {"admin": sign_in(client, "op@twinlm.io", admin["temporary_password"])}


def signup(client, email="ana@acme.io", team="acme"):
    return client.post("/auth/signup", json={"email": email, "name": "Ana", "password": "a-good-password",
                                             "team": team})


def test_signup_is_off_by_default_and_needs_an_admin_first(client, settings, monkeypatch):
    assert signup(client).status_code == 404
    monkeypatch.setattr(settings, "signup_enabled", True)
    assert client.get("/auth/setup").json()["signup_enabled"] is False  # the operator sets up first
    assert signup(client).status_code == 404


def test_signup_gives_a_team_a_budget_and_a_working_key(client, beta):
    assert client.get("/auth/setup").json()["signup_enabled"] is True
    created = signup(client)
    assert created.status_code == 201, created.text
    me = bearer(created.json()["token"])
    team = created.json()["team"]
    assert client.get("/auth/me", headers=me).json()["teams"] == [{"id": team["id"], "name": "acme", "role": "admin"}]
    assert client.get("/admin/keys", headers=me).status_code == 403  # a member, not an org admin

    key = client.post(f"/console/teams/{team['id']}/keys", json={"name": "app", "rpm_limit": 10_000},
                      headers=me).json()
    assert key["rpm_limit"] == 20  # clamped to the beta default
    budgets = client.get(f"/console/teams/{team['id']}/budgets", headers=me).json()
    assert [(b["scope"], b["period"], b["limit_usd"]) for b in budgets] == [("team", "monthly", 5.0)]

    def chat():
        return client.post("/v1/chat/completions", headers=bearer(key["key"]),
                           json={"model": "gemini", "messages": [{"role": "user", "content": "hi"}]})

    assert chat().status_code == 200  # $3 spent
    assert chat().status_code == 429  # $3 + this request's worst case ($3) would cross the $5 team budget


def test_signup_teams_are_isolated(client, beta):
    first = bearer(signup(client).json()["token"])
    second = signup(client, email="bo@other.io", team="other").json()
    assert [t["name"] for t in client.get("/console/teams", headers=first).json()] == ["acme"]
    assert client.get(f"/console/teams/{second['team']['id']}/members", headers=first).status_code == 404


def test_signup_conflicts_cap_and_rate_limit(client, beta, monkeypatch):
    from app.api import teams

    monkeypatch.setattr(teams, "SIGNUPS_PER_MINUTE_PER_IP", 100)
    assert signup(client).status_code == 201
    assert signup(client).status_code == 409  # same email
    assert signup(client, email="new@acme.io").status_code == 409  # same team name
    assert signup(client, email="x@acme.io", team="default").status_code == 400
    assert signup(client, email="bad-email", team="t2").status_code == 422
    assert signup(client, email="b@b.io", team="b").status_code == 201  # 3 accounts: the cap
    assert signup(client, email="c@c.io", team="c").status_code == 403
    assert client.get("/auth/setup").json()["signup_full"] is True

    monkeypatch.setattr(teams, "SIGNUPS_PER_MINUTE_PER_IP", 0)
    assert signup(client, email="d@d.io", team="d").status_code == 429


def test_team_admins_cant_add_accounts_past_the_cap(client, beta):
    owner = signup(client).json()
    me = bearer(owner["token"])

    def add(email):
        return client.post(f"/console/teams/{owner['team']['id']}/members", json={"email": email}, headers=me)

    assert add("colleague@acme.io").status_code == 201  # 3 accounts now
    assert add("another@acme.io").status_code == 403
