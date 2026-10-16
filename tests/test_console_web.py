"""What the web console relies on: cookie sessions with CSRF protection,
first-run setup, the session-based playground and daily usage."""
import pytest

from app.gateway.rate_limit import get_rate_limiter

CONSOLE = {"X-TwinLM-Console": "1"}


@pytest.fixture(autouse=True)
def auth_on(settings, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "admin-token")
    monkeypatch.setattr(settings, "auth_enabled", True)
    get_rate_limiter.cache_clear()
    yield
    get_rate_limiter.cache_clear()


def setup_admin(client):
    assert client.get("/auth/setup").json() == {"needs_setup": True}
    r = client.post("/auth/setup", json={"admin_token": "admin-token", "email": "Boss@acme.io",
                                         "name": "Boss", "password": "a-good-password"})
    assert r.status_code == 200 and "twinlm_session" in r.cookies
    return r


def test_first_run_setup(client):
    bad = client.post("/auth/setup", json={"admin_token": "nope", "email": "a@acme.io", "name": "A",
                                           "password": "a-good-password"})
    assert bad.status_code == 401
    setup_admin(client)
    assert client.get("/auth/setup").json() == {"needs_setup": False}
    again = client.post("/auth/setup", json={"admin_token": "admin-token", "email": "b@acme.io", "name": "B",
                                             "password": "a-good-password"})
    assert again.status_code == 409


def test_cookie_session_and_csrf(client):
    setup_admin(client)  # the TestClient keeps the cookie
    assert client.get("/auth/me").json()["email"] == "boss@acme.io"
    assert client.get("/admin/config/providers").status_code == 200  # admin API via cookie

    # writes with the cookie need the console header
    assert client.post("/admin/teams", json={"name": "eng"}).status_code == 403
    assert client.post("/admin/teams", json={"name": "eng"}, headers=CONSOLE).status_code == 201

    client.post("/auth/logout", headers=CONSOLE)
    assert client.get("/auth/me").status_code == 401
    assert client.get("/admin/config/providers").status_code == 401


def test_login_sets_cookie(client):
    setup_admin(client)
    client.cookies.clear()
    r = client.post("/auth/login", json={"email": "boss@acme.io", "password": "a-good-password"})
    assert r.status_code == 200 and "twinlm_session" in r.cookies
    assert client.get("/console/teams").status_code == 200


def test_playground_uses_the_session_and_bills_the_team(client):
    setup_admin(client)
    team = client.post("/admin/teams", json={"name": "eng"}, headers=CONSOLE).json()
    other = client.post("/admin/teams", json={"name": "sales"}, headers=CONSOLE).json()
    member = client.post(f"/console/teams/{team['id']}/members", json={"email": "dev@acme.io"},
                         headers=CONSOLE).json()

    # sign in as the member
    client.cookies.clear()
    client.post("/auth/login", json={"email": "dev@acme.io", "password": member["temporary_password"]})
    client.post("/auth/password", json={"current_password": member["temporary_password"],
                                        "new_password": "a-good-password"}, headers=CONSOLE)

    body = {"messages": [{"role": "user", "content": "hi"}]}
    assert client.post("/v1/chat/completions", json=body).status_code == 401  # no team chosen
    assert client.post("/v1/chat/completions", json=body, headers={**CONSOLE, "X-TwinLM-Team": "eng"}).status_code == 200
    assert client.post("/v1/chat/completions", json=body,
                       headers={**CONSOLE, "X-TwinLM-Team": "sales"}).status_code == 403
    assert client.post("/v1/chat/completions", json=body, headers={"X-TwinLM-Team": "eng"}).status_code == 403  # CSRF

    mine = client.get(f"/console/teams/{team['id']}/usage").json()
    assert mine["scope"] == "mine" and mine["total"]["requests"] == 1
    days = client.get(f"/console/teams/{team['id']}/usage/daily").json()["days"]
    assert sum(d["requests"] for d in days) == 1
    assert client.get(f"/console/teams/{other['id']}/usage/daily").status_code == 404


def test_admin_daily_usage(client):
    setup_admin(client)
    days = client.get("/admin/usage/daily", params={"days": 7}).json()
    assert days["days"] == [] or {"date", "requests", "cost_usd", "cache_hits"} <= set(days["days"][0])
