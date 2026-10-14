"""Usage and cost attribution: unknown stays unknown, thinking tokens are
billed, cache hits are not provider consumption, deployments are recorded."""
from sqlalchemy import text

from app.gateway.config import PriceConfig
from app.providers.base import TokenUsage
from app.providers.gemini import GeminiProvider
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.router.model_router import ModelRouter
from tests.conftest import FakeProvider, build_services

ADMIN = {"Authorization": "Bearer admin-token"}


def test_gemini_bills_thinking_and_tool_prompt_tokens():
    usage = GeminiProvider._extract_usage({"usageMetadata": {
        "promptTokenCount": 10, "toolUsePromptTokenCount": 2, "candidatesTokenCount": 5,
        "thoughtsTokenCount": 30, "totalTokenCount": 47,
    }})
    assert usage == TokenUsage(12, 35, 47)
    assert GeminiProvider._extract_usage({"usageMetadata": {"totalTokenCount": 9}}) is None


def test_openai_compatible_missing_counts_are_unknown_not_zero():
    extract = OpenAICompatibleProvider._extract_usage
    assert extract({"usage": {"prompt_tokens": 4}}) is None
    assert extract({"usage": {"prompt_tokens": 4, "completion_tokens": 3}}) == TokenUsage(4, 3, 7)


def test_usage_summary_separates_known_cost_cache_hits_and_deployments(client, gateway, settings, monkeypatch, sqlite_engine):
    monkeypatch.setattr(settings, "admin_token", "admin-token")
    priced = FakeProvider("priced", usage=TokenUsage(1000, 1000, 2000))
    unpriced = FakeProvider("unpriced", usage=TokenUsage(10, 10, 20))
    gateway["router"] = ModelRouter(
        {"priced": priced, "unpriced": unpriced}, deployments={"priced-eu": ("priced", "m", None)},
    )
    gateway["services"] = build_services(cache={"enabled": True})
    gateway["services"].config.pricing["priced/m"] = PriceConfig(input_per_million=1, output_per_million=2)

    def chat(model, content):
        return client.post("/v1/chat/completions", json={"model": model, "messages": [{"role": "user", "content": content}]})

    assert chat("priced-eu", "same").status_code == 200
    assert chat("priced-eu", "same").headers["X-Cache"] == "HIT"
    assert chat("unpriced", "other").status_code == 200

    with sqlite_engine.connect() as conn:
        deployments = [r[0] for r in conn.execute(text("SELECT deployment FROM request_logs ORDER BY created_at"))]
    assert deployments == ["priced-eu", "priced-eu", "unpriced/unpriced"]

    usage = client.get("/admin/usage", params={"group_by": "deployment"}, headers=ADMIN).json()
    by_deployment = {g["deployment"]: g for g in usage["groups"]}
    assert by_deployment["priced-eu"]["requests"] == 2
    assert by_deployment["priced-eu"]["total_tokens"] == 2000  # the cache hit consumed no provider tokens
    assert by_deployment["priced-eu"]["cost_usd"] == 0.003
    assert usage["total"]["unpriced_requests"] == 1
    assert usage["total"]["cost_complete"] is False
