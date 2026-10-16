"""Shared pytest fixtures - isolated in-memory DB and fake providers, so
tests never touch a real database or need real API keys."""
import os

# has to happen before anything imports app.*, so the app's own engine
# (only used by the startup event during tests) doesn't touch a real db file
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
# tests opt into auth per test (the `auth_enabled` fixture); the web UI
# router is only mounted when this is set at import time
os.environ.setdefault("AUTH_ENABLED", "false")
os.environ.setdefault("WEB_UI_ENABLED", "true")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import models  # noqa: F401 - registers models with Base
from app.database.database import Base
from app.providers.base import GenerationResult, LLMProvider, ProviderUnavailableError, StreamEvent


class FakeProvider(LLMProvider):
    """Stand-in for a real provider. `behavior` is "ok" (returns `reply`),
    "unavailable" (raises ProviderUnavailableError), "error" (raises a
    plain ValueError, like a normal client error that shouldn't fall back),
    "unavailable_mid_stream" (streaming only - yields `chunks`, then raises
    ProviderUnavailableError, simulating a connection that drops partway
    through a reply instead of failing to connect at all), or "crash"
    (raises a plain RuntimeError - a stand-in for a genuinely unexpected
    failure, e.g. a real non-retryable error from the provider or a bug
    parsing its response - neither availability-related nor a normal
    client error).

    `chunks`, if given, is what `generate_stream` yields piece by piece
    instead of `reply` as one chunk - lets a test assert on streaming
    granularity without needing a real provider. `usage`, if given, is
    reported on `generate`'s result and on the last streamed event, same
    as a real provider only reporting it once the full turn is known.
    """

    def __init__(self, name: str, behavior: str = "ok", reply: str = "canned reply", chunks=None, usage=None):
        self.name = name
        self.behavior = behavior
        self.reply = reply
        self.chunks = chunks
        self.usage = usage
        self.calls = 0
        self.stream_calls = 0
        self.last_params = None

    async def generate(self, messages, model, params=None):
        self.last_params = params
        self.calls += 1
        if self.behavior == "ok":
            return GenerationResult(content=self.reply, usage=self.usage)
        if self.behavior == "unavailable":
            raise ProviderUnavailableError(f"{self.name} unavailable")
        if self.behavior == "error":
            raise ValueError(f"{self.name} bad request")
        if self.behavior == "crash":
            raise RuntimeError(f"{self.name} did something unexpected")
        raise AssertionError(f"unknown behavior: {self.behavior}")

    async def generate_stream(self, messages, model, params=None):
        self.last_params = params
        self.stream_calls += 1
        if self.behavior == "unavailable":
            raise ProviderUnavailableError(f"{self.name} unavailable")
        if self.behavior == "error":
            raise ValueError(f"{self.name} bad request")
        if self.behavior == "crash":
            raise RuntimeError(f"{self.name} did something unexpected")
        if self.behavior in ("ok", "unavailable_mid_stream"):
            pieces = self.chunks or [self.reply]
            for i, chunk in enumerate(pieces):
                is_last = i == len(pieces) - 1
                yield StreamEvent(delta=chunk, usage=self.usage if is_last else None)
            if self.behavior == "unavailable_mid_stream":
                raise ProviderUnavailableError(f"{self.name} dropped mid-stream")
            return
        raise AssertionError(f"unknown behavior: {self.behavior}")


@pytest.fixture
def sqlite_engine():
    """Fresh in-memory SQLite engine with all tables created. StaticPool
    keeps one shared connection so every session sees the same data -
    otherwise each connection would get its own empty in-memory db."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def db_session(sqlite_engine):
    """A SQLAlchemy session bound to the fresh test database."""
    session_factory = sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)
    session = session_factory()
    yield session
    session.close()


@pytest.fixture
def fake_providers():
    """gemini + groq, both healthy by default - mutate `.behavior` to
    simulate an outage."""
    return {
        "gemini": FakeProvider("gemini", behavior="ok", reply="hello from gemini"),
        "groq": FakeProvider("groq", behavior="ok", reply="hello from groq"),
    }


def build_services(**sections):
    """Gateway services from an isolated config - tests never read the
    repo's gateway.toml. `sections` are GatewayConfig fields."""
    from app.api.common import GatewayServices, semantic_cache_for
    from app.gateway.cache import ResponseCache
    from app.gateway.config import GatewayConfig
    from app.gateway.guardrails import GuardrailEngine
    from app.gateway.rate_limit import SlidingWindowLimiter

    config = GatewayConfig.model_validate({"providers": {}, **sections})
    return GatewayServices(
        config=config,
        limiter=SlidingWindowLimiter(),
        cache=ResponseCache(config.cache.ttl_seconds, config.cache.max_entries),
        guardrails=GuardrailEngine(config.guardrails),
        semantic_cache=semantic_cache_for(config),
    )


@pytest.fixture
def gateway(fake_providers):
    """Mutable wiring for the `client` fixture: replace `gateway["router"]`
    or `gateway["services"]` (e.g. via `build_services`) inside a test."""
    from app.router.model_router import ModelRouter

    return {"router": ModelRouter(fake_providers), "services": build_services()}


@pytest.fixture
def client(sqlite_engine, gateway):
    """A FastAPI TestClient wired to the isolated test database, fake
    providers and an isolated gateway config."""
    from fastapi.testclient import TestClient

    from app.api.common import get_services
    from app.database.database import get_db
    from app.main import app
    from app.router.model_router import get_model_router

    session_factory = sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)

    def override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_model_router] = lambda: gateway["router"]
    app.dependency_overrides[get_services] = lambda: gateway["services"]

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def settings(monkeypatch):
    """The live Settings object; use monkeypatch.setattr(settings, ...) to
    flip auth_enabled / admin_token for one test."""
    from app.config import get_settings

    return get_settings()
