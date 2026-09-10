"""Shared pytest fixtures - isolated in-memory DB and fake providers, so
tests never touch a real database or need real API keys."""
import os

# has to happen before anything imports app.*, so the app's own engine
# (only used by the startup event during tests) doesn't touch a real db file
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import models  # noqa: F401 - registers models with Base
from app.database.database import Base
from app.providers.base import LLMProvider, ProviderUnavailableError


class FakeProvider(LLMProvider):
    """Stand-in for a real provider. `behavior` is "ok" (returns `reply`),
    "unavailable" (raises ProviderUnavailableError), "error" (raises a
    plain ValueError, like a normal client error that shouldn't fall back),
    or "unavailable_mid_stream" (streaming only - yields `chunks`, then
    raises ProviderUnavailableError, simulating a connection that drops
    partway through a reply instead of failing to connect at all).

    `chunks`, if given, is what `generate_stream` yields piece by piece
    instead of `reply` as one chunk - lets a test assert on streaming
    granularity without needing a real provider.
    """

    def __init__(self, name: str, behavior: str = "ok", reply: str = "canned reply", chunks=None):
        self.name = name
        self.behavior = behavior
        self.reply = reply
        self.chunks = chunks
        self.calls = 0
        self.stream_calls = 0

    async def generate(self, messages, model):
        self.calls += 1
        if self.behavior == "ok":
            return self.reply
        if self.behavior == "unavailable":
            raise ProviderUnavailableError(f"{self.name} unavailable")
        if self.behavior == "error":
            raise ValueError(f"{self.name} bad request")
        raise AssertionError(f"unknown behavior: {self.behavior}")

    async def generate_stream(self, messages, model):
        self.stream_calls += 1
        if self.behavior == "unavailable":
            raise ProviderUnavailableError(f"{self.name} unavailable")
        if self.behavior == "error":
            raise ValueError(f"{self.name} bad request")
        if self.behavior in ("ok", "unavailable_mid_stream"):
            for chunk in self.chunks or [self.reply]:
                yield chunk
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


@pytest.fixture
def client(sqlite_engine, fake_providers):
    """A FastAPI TestClient wired to the isolated test database and fake providers."""
    from fastapi.testclient import TestClient

    from app.database.database import get_db
    from app.main import app
    from app.router.model_router import ModelRouter, get_model_router

    session_factory = sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)

    def override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_model_router] = lambda: ModelRouter(fake_providers)

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
