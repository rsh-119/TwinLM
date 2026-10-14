"""Cost controls: output-token caps, cache eligibility/TTL, savings report."""
import pytest

from app.gateway.cache import ResponseCache
from app.gateway.config import GatewayConfig
from app.providers.base import TokenUsage
from app.router.model_router import ModelRouter
from tests.conftest import build_services

ADMIN = {"Authorization": "Bearer admin-token"}
PRICING = {
    "gemini/gemini-flash-latest": {"input_per_million": 0.3, "output_per_million": 2.5},
    "openai/gpt-4o": {"input_per_million": 2.5, "output_per_million": 10.0},
}


def _chat(client, content="hi", **extra):
    return client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": content}], **extra})


def test_output_cap_fills_in_and_clamps_max_tokens(client, gateway, fake_providers):
    gateway["router"] = ModelRouter(fake_providers, {"auto": ["gemini"], "short": ["gemini"]})
    gateway["services"] = build_services(
        default_max_output_tokens=100,
        models={"auto": {"targets": ["gemini"]}, "short": {"targets": ["gemini"], "max_output_tokens": 20}},
    )
    seen = lambda: fake_providers["gemini"].last_params["max_tokens"]  # noqa: E731

    _chat(client)
    assert seen() == 100
    _chat(client, max_tokens=50)
    assert seen() == 50
    _chat(client, max_tokens=500)
    assert seen() == 100
    _chat(client, model="short", max_tokens=500)
    assert seen() == 20
    client.post("/v1/chat/completions", json={"model": "short", "stream": True, "messages": [{"role": "user", "content": "s"}]})
    assert seen() == 20


def test_cache_skips_sampled_requests_and_aliases_with_ttl_zero(client, gateway, fake_providers):
    gateway["router"] = ModelRouter(fake_providers, {"auto": ["gemini"], "live": ["gemini"]})
    gateway["services"] = build_services(
        cache={"enabled": True},
        models={"auto": {"targets": ["gemini"]}, "live": {"targets": ["gemini"], "cache_ttl_seconds": 0}},
    )

    for _ in range(2):
        _chat(client, "creative", temperature=0.8)
        _chat(client, "fresh", model="live")
    assert fake_providers["gemini"].calls == 4
    _chat(client, "stable", temperature=0)
    assert _chat(client, "stable", temperature=0).headers["X-Cache"] == "HIT"


def test_per_entry_cache_ttl():
    now = [0.0]
    cache = ResponseCache(ttl_seconds=300, max_entries=10, clock=lambda: now[0])
    cache.set("short", 1, ttl_seconds=5)
    cache.set("default", 2)
    now[0] = 6
    assert cache.get("short") is None and cache.get("default") == 2


def test_savings_report_against_a_baseline(client, gateway, fake_providers, settings, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "admin-token")
    fake_providers["gemini"].usage = TokenUsage(1000, 1000, 2000)
    fake_providers["groq"].usage = TokenUsage(10, 10, 20)  # unpriced: not measurable
    gateway["services"] = build_services(cache={"enabled": True}, pricing=PRICING, savings={"baseline": "openai/gpt-4o"})

    _chat(client, "a")
    assert _chat(client, "a").headers["X-Cache"] == "HIT"
    _chat(client, "b", model="groq")

    report = client.get("/admin/savings", headers=ADMIN).json()
    total = report["total"]
    assert (total["requests"], total["measured_requests"], total["cache_hits"]) == (3, 2, 1)
    assert total["actual_cost_usd"] == 0.0028        # one paid gemini call
    assert total["baseline_cost_usd"] == 0.025       # two measured requests on gpt-4o prices
    assert total["cache_avoided_usd"] == 0.0028
    assert total["savings_usd"] == 0.0222 and total["savings_pct"] == 88.8
    assert total["complete"] is False                # the groq request has no price
    assert {g["project"] for g in report["groups"]} == {None}


def test_savings_needs_a_priced_baseline(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "admin-token")
    assert client.get("/admin/savings", headers=ADMIN).status_code == 404
    with pytest.raises(ValueError, match="needs a \\[pricing\\] entry"):
        GatewayConfig.model_validate({"providers": {}, "savings": {"baseline": "openai/gpt-4o"}})
