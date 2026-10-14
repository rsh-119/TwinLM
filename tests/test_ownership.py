"""User ownership of conversations/memories, key rotation, the optional
stateful layer, and strict request fields."""
import pytest

ADMIN = {"Authorization": "Bearer admin-token"}
TURN = [{"role": "user", "content": "hi"}]


@pytest.fixture
def auth(settings, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "admin-token")
    monkeypatch.setattr(settings, "auth_enabled", True)
    return settings


def _key(client, project="acme", **fields):
    body = client.post("/admin/keys", json={"name": "k", "project": project, **fields}, headers=ADMIN).json()
    return body, {"Authorization": f"Bearer {body['key']}"}


def test_user_bound_keys_only_see_their_own_data(client, auth):
    _, alice = _key(client, user_id="alice")
    _, bob = _key(client, user_id="bob")
    _, alice_elsewhere = _key(client, project="other", user_id="alice")

    created = client.post("/v1/conversations", json={}, headers=alice)
    assert created.status_code == 201
    conv = created.json()["conversation_id"]
    assert client.get(f"/v1/conversations/{conv}", headers=alice).json()["user_id"] == "alice"
    assert client.post("/v1/memories", json={"content": "likes tea"}, headers=alice).json()["user_id"] == "alice"
    turn = {"conversation_id": conv, "messages": TURN}

    for intruder in (bob, alice_elsewhere):  # other user; same user name in another project
        assert client.get(f"/v1/conversations/{conv}", headers=intruder).status_code == 404
        assert client.get(f"/v1/conversations/{conv}/messages", headers=intruder).status_code == 404
        assert client.post("/v1/chat/completions", json=turn, headers=intruder).status_code == 404
        assert client.post("/v1/chat/completions", json={**turn, "stream": True}, headers=intruder).status_code == 404
        assert client.delete(f"/v1/conversations/{conv}", headers=intruder).status_code == 404
    assert client.get("/v1/memories/alice", headers=bob).status_code == 403
    assert client.post("/v1/memories", json={"user_id": "alice", "content": "x"}, headers=bob).status_code == 403
    assert client.post("/v1/conversations", json={"user_id": "alice"}, headers=bob).status_code == 403
    assert client.get("/v1/memories/alice", headers=alice_elsewhere).json() == []

    assert client.post("/v1/chat/completions", json=turn, headers=alice).status_code == 200
    assert [m["content"] for m in client.get("/v1/memories/alice", headers=alice).json()] == ["likes tea"]


def test_service_keys_act_for_users_unless_user_keys_are_required(client, auth, monkeypatch):
    _, service = _key(client)
    assert client.post("/v1/conversations", json={}, headers=service).status_code == 400  # user_id required
    conv = client.post("/v1/conversations", json={"user_id": "carol"}, headers=service).json()["conversation_id"]
    assert client.get(f"/v1/conversations/{conv}", headers=service).status_code == 200

    monkeypatch.setattr(auth, "stateful_requires_user_keys", True)
    assert client.get(f"/v1/conversations/{conv}", headers=service).status_code == 403
    assert client.get("/v1/memories/carol", headers=service).status_code == 403
    turn = {"conversation_id": conv, "messages": TURN}
    assert client.post("/v1/chat/completions", json=turn, headers=service).status_code == 403
    assert client.post("/v1/chat/completions", json={"messages": TURN}, headers=service).status_code == 200


def test_rotation_keeps_settings_and_can_revoke_the_old_key(client, auth):
    old, old_headers = _key(client, user_id="alice", rpm_limit=5, allowed_models=["auto"])

    rotated = client.post(f"/admin/keys/{old['id']}/rotate", headers=ADMIN).json()
    new_headers = {"Authorization": f"Bearer {rotated['key']}"}
    assert (rotated["user_id"], rotated["rpm_limit"], rotated["allowed_models"]) == ("alice", 5, ["auto"])
    assert client.get("/v1/models", headers=old_headers).status_code == 200  # grace until revoked
    assert client.get("/v1/models", headers=new_headers).status_code == 200

    again = client.post(f"/admin/keys/{rotated['id']}/rotate?revoke_old=true", headers=ADMIN).json()
    assert client.get("/v1/models", headers=new_headers).status_code == 401
    assert client.get("/v1/models", headers={"Authorization": f"Bearer {again['key']}"}).status_code == 200
    assert client.post(f"/admin/keys/{rotated['id']}/rotate", headers=ADMIN).status_code == 404


def test_models_list_respects_the_key_allow_list(client, auth):
    _, headers = _key(client, allowed_models=["auto"])
    assert [m["id"] for m in client.get("/v1/models", headers=headers).json()["data"]] == ["auto"]


def test_stateful_mode_can_be_switched_off(client, settings, monkeypatch):
    conv = client.post("/v1/conversations", json={"user_id": "u"}).json()["conversation_id"]
    monkeypatch.setattr(settings, "stateful_enabled", False)

    response = client.post("/v1/chat/completions", json={"conversation_id": conv, "messages": TURN})

    assert response.status_code == 400 and "stateful mode is disabled" in response.json()["error"]["message"]
    assert client.post("/v1/chat/completions", json={"messages": TURN}).status_code == 200


def test_unknown_fields_are_rejected_but_null_echoes_are_tolerated(client):
    ok = client.post("/v1/chat/completions", json={"messages": TURN, "logit_bias": None, "user": "end-user-1"})
    assert ok.status_code == 200

    top = client.post("/v1/chat/completions", json={"messages": TURN, "top_logprobs": 2})
    nested = client.post("/v1/chat/completions", json={"messages": [{**TURN[0], "audio": {"id": "a"}}]})
    embed = client.post("/v1/embeddings", json={"model": "auto", "input": "x", "dimensions": 8})
    for response, param in ((top, "top_logprobs"), (nested, "messages.0.audio"), (embed, "dimensions")):
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "unsupported_parameter"
        assert response.json()["error"]["param"] == param

    conflict = client.post("/v1/chat/completions", json={"messages": TURN, "max_tokens": 5, "max_completion_tokens": 6})
    assert conflict.status_code == 422


def test_auth_cache_rides_out_a_database_outage(client, auth, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app.database import repository

    _, headers = _key(client)
    monkeypatch.setattr(auth, "auth_cache_seconds", 0.0001)
    assert client.post("/v1/chat/completions", json={"messages": TURN}, headers=headers).status_code == 200

    def database_down(*_args, **_kwargs):
        raise OperationalError("SELECT", {}, Exception("connection refused"))

    monkeypatch.setattr(repository, "get_api_key_by_hash", database_down)
    # expired cache entry + unreachable database: last validated state is used
    assert client.post("/v1/chat/completions", json={"messages": TURN}, headers=headers).status_code == 200
    unknown = client.post("/v1/chat/completions", json={"messages": TURN},
                          headers={"Authorization": "Bearer gw_live_never-seen"})
    assert unknown.status_code == 503
    assert unknown.headers["Retry-After"] == "5"
    assert unknown.json()["error"]["message"] == "database unavailable"
