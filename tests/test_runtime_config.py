"""Providers, aliases and prices managed from the admin panel: applied at
runtime without a restart, keys encrypted at rest and never returned."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.database.models import ProviderConfigRow
from app.gateway import runtime
from app.gateway.config import GatewayConfig
from tests.conftest import build_services

ADMIN = {"Authorization": "Bearer admin-token"}
BASE = {
    "providers": {"base": {"type": "mock", "default_model": "mock-base"}},
    "models": {"auto": {"targets": ["base"]}},
}


@pytest.fixture
def panel(sqlite_engine, settings, monkeypatch):
    """Client with the real (swappable) router and a test gateway.toml."""
    from app.api.common import get_services
    from app.database.database import get_db
    from app.main import app

    monkeypatch.setattr(settings, "admin_token", "admin-token")
    monkeypatch.setattr(settings, "provider_keys_secret", "test-secret")
    runtime.set_base_config(GatewayConfig.model_validate(BASE))
    services = build_services(**BASE)
    factory = sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)

    def override_get_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_services] = lambda: services
    with TestClient(app) as client:
        client.factory = factory
        yield client
    app.dependency_overrides.clear()
    runtime.set_base_config(None)


def chat(client, model):
    return client.post("/v1/chat/completions", json={"model": model, "messages": [{"role": "user", "content": "hi"}]})


def add(client, name="extra", **extra):
    body = {"name": name, "type": "mock", "default_model": "mock-x", "api_key": "sk-test-abcdef123456", **extra}
    return client.post("/admin/config/providers", json=body, headers=ADMIN)


def test_new_provider_and_alias_work_without_restart(panel):
    assert chat(panel, "fast2").status_code == 400  # unknown yet
    assert add(panel).json()["key_hint"] == "…3456"
    assert panel.put("/admin/config/aliases/fast2", json={"targets": ["extra/mock-x", "base"]},
                     headers=ADMIN).status_code == 200

    reply = chat(panel, "fast2")
    assert reply.status_code == 200 and reply.json()["provider"] == "extra"
    assert chat(panel, "auto").json()["provider"] == "base"  # gateway.toml alias untouched

    # panel alias replaces the toml one; deleting it restores the toml one
    panel.put("/admin/config/aliases/auto", json={"targets": ["extra"]}, headers=ADMIN)
    assert chat(panel, "auto").json()["provider"] == "extra"
    assert panel.delete("/admin/config/aliases/auto", headers=ADMIN).json()["restored_from_toml"] is True
    assert chat(panel, "auto").json()["provider"] == "base"


def test_key_is_encrypted_and_never_returned(panel):
    add(panel)
    listing = panel.get("/admin/config/providers", headers=ADMIN).text
    assert "sk-test-abcdef123456" not in listing
    session = panel.factory()
    row = session.get(ProviderConfigRow, "extra")
    assert row.api_key_encrypted and "sk-test" not in row.api_key_encrypted
    from app.gateway import secret_box
    assert secret_box.decrypt(row.api_key_encrypted) == "sk-test-abcdef123456"

    panel.patch("/admin/config/providers/extra", json={"api_key": "sk-rotated-99998888"}, headers=ADMIN)
    session.expire_all()
    assert secret_box.decrypt(session.get(ProviderConfigRow, "extra").api_key_encrypted) == "sk-rotated-99998888"
    session.close()


def test_disable_falls_back_and_enable_restores(panel):
    add(panel)
    panel.put("/admin/config/aliases/mixed", json={"targets": ["extra", "base"]}, headers=ADMIN)
    assert chat(panel, "mixed").json()["provider"] == "extra"
    panel.patch("/admin/config/providers/extra", json={"enabled": False}, headers=ADMIN)
    assert chat(panel, "mixed").json()["provider"] == "base"
    panel.patch("/admin/config/providers/extra", json={"enabled": True}, headers=ADMIN)
    assert chat(panel, "mixed").json()["provider"] == "extra"


def test_invalid_changes_are_rejected_and_not_stored(panel):
    bad = panel.put("/admin/config/aliases/broken", json={"targets": ["nope/model"]}, headers=ADMIN)
    assert bad.status_code == 400 and "invalid configuration" in bad.json()["detail"]
    assert all(a["alias"] != "broken" for a in panel.get("/admin/config/aliases", headers=ADMIN).json())

    assert add(panel, name="base").status_code == 409  # clashes with gateway.toml
    assert add(panel, name="Bad/Name").status_code == 422
    add(panel)
    panel.put("/admin/config/aliases/uses-extra", json={"targets": ["extra"]}, headers=ADMIN)
    conflict = panel.delete("/admin/config/providers/extra", headers=ADMIN)
    assert conflict.status_code == 409 and "uses-extra" in conflict.json()["detail"]
    panel.delete("/admin/config/aliases/uses-extra", headers=ADMIN)
    assert panel.delete("/admin/config/providers/extra", headers=ADMIN).status_code == 200
    assert panel.patch("/admin/config/providers/base", json={"enabled": False}, headers=ADMIN).status_code == 404


def test_prices_apply_to_cost(panel):
    add(panel)
    panel.put("/admin/config/aliases/priced", json={"targets": ["extra/mock-x"]}, headers=ADMIN)
    panel.put("/admin/config/prices", json={"key": "extra/mock-x", "input_per_million": 1000,
                                            "output_per_million": 1000}, headers=ADMIN)
    prices = {p["key"]: p for p in panel.get("/admin/config/prices", headers=ADMIN).json()}
    assert prices["extra/mock-x"]["source"] == "panel"
    chat(panel, "priced")
    usage = panel.get("/admin/usage", params={"group_by": "provider"}, headers=ADMIN).json()
    assert usage["total"]["cost_usd"] and usage["total"]["cost_complete"] is True


def test_connection_test(panel):
    ok = panel.post("/admin/config/test", json={"type": "mock", "model": "mock-x"}, headers=ADMIN).json()
    assert ok["ok"] is True
    down = panel.post("/admin/config/test", json={"type": "openai_compatible", "base_url": "http://127.0.0.1:9/v1",
                                                  "model": "m", "api_key": "k"}, headers=ADMIN).json()
    assert down["ok"] is False and down["error"]
    presets = panel.get("/admin/config/presets", headers=ADMIN).json()
    assert {"anthropic", "mistral", "openai"} <= set(presets)


def test_saving_a_key_needs_the_secret(panel, settings, monkeypatch):
    monkeypatch.setattr(settings, "provider_keys_secret", "")
    monkeypatch.setattr(settings, "provider_keys_secret_file", "")
    response = add(panel)
    assert response.status_code == 503 and "PROVIDER_KEYS_SECRET" in response.json()["detail"]
    assert add(panel, name="local", api_key=None).status_code == 201  # keyless providers still work


def test_config_survives_a_restart(panel):
    add(panel)
    panel.put("/admin/config/aliases/kept", json={"targets": ["extra"]}, headers=ADMIN)
    session = panel.factory()
    config, keys = runtime.effective_config(session)  # what a fresh process builds at startup
    session.close()
    assert "extra" in config.providers and config.models["kept"].targets == ["extra"]
    assert keys["extra"] == "sk-test-abcdef123456"
    assert json.loads(json.dumps(config.model_dump()))  # serialisable, i.e. a plain valid config
