"""Console users, teams, members, roles and team keys."""
import pytest

from app.gateway.rate_limit import get_rate_limiter

ADMIN_TOKEN = {"Authorization": "Bearer admin-token"}


@pytest.fixture(autouse=True)
def admin_token(settings, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "admin-token")
    get_rate_limiter.cache_clear()
    yield
    get_rate_limiter.cache_clear()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def new_user(client, email, role="member", name="Person"):
    r = client.post("/admin/users", json={"email": email, "name": name, "role": role}, headers=ADMIN_TOKEN)
    assert r.status_code == 201, r.text
    return r.json()


def sign_in(client, email, password, new_password="a-good-password"):
    """Sign in with a temporary password and set a real one; returns headers."""
    token = client.post("/auth/login", json={"email": email, "password": password}).json()["token"]
    assert client.post("/auth/password", json={"current_password": password, "new_password": new_password},
                       headers=bearer(token)).status_code == 200
    return bearer(token)


@pytest.fixture
def org(client):
    """An admin, a team with a team admin and a member, signed in."""
    admin = new_user(client, "Boss@Acme.io", role="admin", name="Boss")
    admin_h = sign_in(client, "boss@acme.io", admin["temporary_password"])
    team = client.post("/admin/teams", json={"name": "platform-eng"}, headers=admin_h).json()
    other = client.post("/admin/teams", json={"name": "sales"}, headers=admin_h).json()
    lead = client.post(f"/console/teams/{team['id']}/members", json={"email": "lead@acme.io", "role": "admin"},
                       headers=admin_h).json()
    dev = client.post(f"/console/teams/{team['id']}/members", json={"email": "dev@acme.io", "name": "Dev"},
                      headers=admin_h).json()
    return {
        "admin": admin_h, "team": team, "other": other,
        "lead": sign_in(client, "lead@acme.io", lead["temporary_password"]), "lead_id": lead["user_id"],
        "dev": sign_in(client, "dev@acme.io", dev["temporary_password"]), "dev_id": dev["user_id"],
    }


def test_sign_in_flow_and_passwords(client):
    created = new_user(client, "a@acme.io", role="admin")
    assert "password_hash" not in created and created["must_change_password"] is True
    assert client.post("/auth/login", json={"email": "a@acme.io", "password": "wrong"}).status_code == 401

    token = client.post("/auth/login", json={"email": "A@acme.io", "password": created["temporary_password"]}).json()["token"]
    assert client.get("/console/teams", headers=bearer(token)).status_code == 403  # must set a password first
    assert client.get("/admin/keys", headers=bearer(token)).status_code == 403
    client.post("/auth/password", json={"current_password": created["temporary_password"],
                                        "new_password": "a-good-password"}, headers=bearer(token))
    me = client.get("/auth/me", headers=bearer(token)).json()
    assert me["email"] == "a@acme.io" and me["must_change_password"] is False
    assert client.get("/admin/keys", headers=bearer(token)).status_code == 200  # admin session works on /admin

    client.post("/auth/logout", headers=bearer(token))
    assert client.get("/auth/me", headers=bearer(token)).status_code == 401
    # 10 attempts a minute per email (2 used above)
    assert all(r.status_code == 401 for r in
               [client.post("/auth/login", json={"email": "a@acme.io", "password": "x"}) for _ in range(8)])
    assert client.post("/auth/login", json={"email": "a@acme.io", "password": "x"}).status_code == 429


def test_disabled_users_and_last_admin(client):
    admin = new_user(client, "a@acme.io", role="admin")
    other = new_user(client, "b@acme.io")
    client.patch(f"/admin/users/{other['id']}", json={"disabled": True}, headers=ADMIN_TOKEN)
    assert client.post("/auth/login", json={"email": "b@acme.io",
                                            "password": other["temporary_password"]}).status_code == 401
    assert client.patch(f"/admin/users/{admin['id']}", json={"role": "member"}, headers=ADMIN_TOKEN).status_code == 409
    reset = client.patch(f"/admin/users/{other['id']}", json={"disabled": False, "reset_password": True},
                         headers=ADMIN_TOKEN).json()
    assert client.post("/auth/login", json={"email": "b@acme.io",
                                            "password": reset["temporary_password"]}).status_code == 200


def test_roles_scope_members_and_keys(client, org):
    team, other = org["team"]["id"], org["other"]["id"]
    # members see their team, not others; admins see all
    assert [t["name"] for t in client.get("/console/teams", headers=org["dev"]).json()] == ["platform-eng"]
    assert len(client.get("/console/teams", headers=org["admin"]).json()) == 2
    assert client.get(f"/console/teams/{other}/members", headers=org["lead"]).status_code == 404

    # members can't manage people; team admins can, only in their team
    assert client.post(f"/console/teams/{team}/members", json={"email": "x@acme.io"},
                       headers=org["dev"]).status_code == 403
    assert client.post(f"/console/teams/{other}/members", json={"email": "x@acme.io"},
                       headers=org["lead"]).status_code == 404
    assert client.patch(f"/console/teams/{team}/members/{org['dev_id']}", json={"role": "member"},
                        headers=org["lead"]).status_code == 200

    # keys: members only their own; team admins any in the team, incl. service keys
    mine = client.post(f"/console/teams/{team}/keys", json={"name": "laptop"}, headers=org["dev"]).json()
    assert mine["team"] == "platform-eng" and mine["user_id"] == org["dev_id"] and mine["key"].startswith("gw_live_")
    assert client.post(f"/console/teams/{team}/keys", json={"name": "svc", "for_user": None},
                       headers=org["dev"]).status_code == 403
    service = client.post(f"/console/teams/{team}/keys", json={"name": "support-bot", "for_user": None},
                          headers=org["lead"]).json()
    assert service["kind"] == "service"
    assert [k["name"] for k in client.get(f"/console/teams/{team}/keys", headers=org["dev"]).json()] == ["laptop"]
    assert len(client.get(f"/console/teams/{team}/keys", headers=org["lead"]).json()) == 2
    assert client.delete(f"/console/teams/{team}/keys/{service['id']}", headers=org["dev"]).status_code == 403

    # members list shows people and service keys
    rows = client.get(f"/console/teams/{team}/members", headers=org["dev"]).json()
    assert {r["role"] for r in rows} == {"admin", "member", "service"}

    # member sessions can't touch org admin endpoints
    assert client.get("/admin/config/providers", headers=org["dev"]).status_code == 403
    assert client.post("/admin/teams", json={"name": "new"}, headers=org["lead"]).status_code == 403


def test_team_keys_work_and_usage_is_scoped(client, org, settings, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    team = org["team"]["id"]
    dev_key = client.post(f"/console/teams/{team}/keys", json={"name": "dev"}, headers=org["dev"]).json()["key"]
    svc_key = client.post(f"/console/teams/{team}/keys", json={"name": "bot", "for_user": None},
                          headers=org["lead"]).json()["key"]
    for key in (dev_key, svc_key, svc_key):
        assert client.post("/v1/chat/completions", headers=bearer(key),
                           json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 200

    assert client.get(f"/console/teams/{team}/usage", headers=org["lead"]).json()["total"]["requests"] == 3
    mine = client.get(f"/console/teams/{team}/usage", headers=org["dev"]).json()
    assert mine["scope"] == "mine" and mine["total"]["requests"] == 1

    rows = {r["name"]: r for r in client.get(f"/console/teams/{team}/members", headers=org["lead"]).json()}
    assert rows["Dev"]["last_active"] and rows["bot"]["last_active"]

    # removing a member revokes their keys in the team
    removed = client.delete(f"/console/teams/{team}/members/{org['dev_id']}", headers=org["lead"]).json()
    assert removed["revoked_keys"] == 1
    assert client.post("/v1/chat/completions", headers=bearer(dev_key),
                       json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401

    # a team with active keys can't be deleted
    assert client.delete(f"/admin/teams/{team}", headers=org["admin"]).status_code == 409


def test_azure_style_api_key_header(monkeypatch):
    from app.providers.openai_compatible import OpenAICompatibleProvider

    azure = OpenAICompatibleProvider("azure", "https://x.openai.azure.com/openai/v1", "k-123", "gpt-4o",
                                     auth_header="api-key")
    assert azure._headers() == {"api-key": "k-123"}
    plain = OpenAICompatibleProvider("openai", "https://api.openai.com/v1", "k-123", "gpt-4o")
    assert plain._headers() == {"Authorization": "Bearer k-123"}


def test_expired_session_is_rejected(client, sqlite_engine):
    from datetime import datetime, timedelta

    from sqlalchemy.orm import Session

    from app.database.models import UserSession

    created = new_user(client, "a@acme.io", role="admin")
    headers = sign_in(client, "a@acme.io", created["temporary_password"])
    with Session(sqlite_engine) as db:
        db.query(UserSession).update({UserSession.expires_at: datetime.utcnow() - timedelta(minutes=1)})
        db.commit()
    assert client.get("/auth/me", headers=headers).status_code == 401
    assert client.get("/admin/keys", headers=headers).status_code == 401


def test_create_admin_command(tmp_path, monkeypatch, capsys):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import ops
    from app.database import database

    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    assert ops.main(["create-admin", "--email", "Owner@Acme.io", "--name", "Owner"]) == 0
    assert "Temporary password" in capsys.readouterr().out
    assert ops.main(["create-admin", "--email", "owner@acme.io"]) == 1
