"""Semantic cache: a differently worded prompt with the same meaning reuses
the cached answer; only for listed aliases, scoped by everything except
the last user message."""
from sqlalchemy import text

from app.gateway.semantic_cache import SemanticCache
from app.providers.base import EmbeddingResult, ProviderUnavailableError, TokenUsage
from app.router.model_router import ModelRouter
from tests.conftest import FakeProvider, build_services

VECTORS = {
    "when are you open?": [1.0, 0.0, 0.0],
    "what are your opening hours?": [0.98, 0.2, 0.0],  # similarity ~0.98
    "where is your office?": [0.0, 1.0, 0.0],
}


class Embedder(FakeProvider):
    async def embed(self, texts, model):
        self.calls += 1
        if self.behavior == "unavailable":
            raise ProviderUnavailableError("embedder down")
        return EmbeddingResult([VECTORS[t] for t in texts], TokenUsage(3, 0, 3))


def _setup(gateway, fake_providers, **semantic):
    embedder = Embedder("emb")
    gateway["router"] = ModelRouter({**fake_providers, "emb": embedder},
                                    {"faq": ["gemini"], "auto": ["gemini"], "embed": ["emb"]})
    gateway["services"] = build_services(cache={"enabled": True, "semantic": {"models": ["faq"], **semantic}})
    return embedder


def _ask(client, question, model="faq", system=None):
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": question}]
    return client.post("/v1/chat/completions", json={"model": model, "messages": messages})


def test_same_meaning_is_served_from_cache(client, gateway, fake_providers, sqlite_engine):
    embedder = _setup(gateway, fake_providers)
    assert _ask(client, "when are you open?").headers["X-Cache"] == "MISS"
    hit = _ask(client, "what are your opening hours?")
    assert hit.headers["X-Cache"] == "HIT" and float(hit.headers["X-Cache-Similarity"]) > 0.97
    assert hit.json()["choices"][0]["message"]["content"] == "hello from gemini"
    assert _ask(client, "where is your office?").headers["X-Cache"] == "MISS"
    assert (fake_providers["gemini"].calls, embedder.calls) == (2, 3)

    with sqlite_engine.connect() as conn:
        rows = conn.execute(text("SELECT endpoint, cache_hit FROM request_logs ORDER BY created_at")).all()
    assert [tuple(r) for r in rows].count(("embeddings", 0)) == 3  # each embedding call is logged
    assert ("chat", 1) in [tuple(r) for r in rows]


def test_only_listed_aliases_and_same_context(client, gateway, fake_providers):
    embedder = _setup(gateway, fake_providers)
    _ask(client, "when are you open?", model="auto")
    assert _ask(client, "what are your opening hours?", model="auto").headers["X-Cache"] == "MISS"
    assert embedder.calls == 0  # "auto" isn't in [cache.semantic] models

    _ask(client, "when are you open?", system="You are the Paris shop")
    other_shop = _ask(client, "what are your opening hours?", system="You are the Berlin shop")
    assert other_shop.headers["X-Cache"] == "MISS"  # earlier messages must match exactly


def test_threshold_and_embedding_failure(client, gateway, fake_providers):
    embedder = _setup(gateway, fake_providers, min_similarity=0.99)
    _ask(client, "when are you open?")
    assert _ask(client, "what are your opening hours?").headers["X-Cache"] == "MISS"  # 0.98 < 0.99

    embedder.behavior = "unavailable"
    response = _ask(client, "when are you open?")
    assert response.status_code == 200  # served uncached, never failed by the cache


def test_eviction_and_expiry():
    now = [0.0]
    cache = SemanticCache(min_similarity=0.9, max_entries=2, ttl_seconds=10, clock=lambda: now[0])
    cache.set("s", [1.0, 0.0], "a")
    cache.set("s", [0.0, 1.0], "b")
    cache.set("t", [1.0, 1.0], "c")
    assert len(cache) == 2 and cache.get("s", [1.0, 0.0]) is None  # oldest evicted
    assert cache.get("s", [0.0, 1.0]) == ("b", 1.0)
    assert cache.get("s", [0.0, 0.0]) is None  # zero vector never matches
    now[0] = 11
    assert cache.get("s", [0.0, 1.0]) is None
