"""Shared pytest fixtures.

Every test gets a fresh, isolated in-memory SQLite database and fake
providers - nothing here touches a real database file or needs real
provider API keys.
"""
import os

# Set before anything imports app.* so the app's own module-level engine
# (used only by the startup event in tests) never touches a real db file.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import models  # noqa: F401 - registers models with Base
from app.database.database import Base
from app.providers.base import LLMProvider, ProviderUnavailableError


class FakeProvider(LLMProvider):
    """A configurable stand-in for a real provider, used in tests only.

    `behavior` controls what `generate()` does:
      - "ok": returns `reply`.
      - "unavailable": raises ProviderUnavailableError (retryable failure).
      - "error": raises a plain ValueError (a normal client error that
        should never trigger fallback).
    """

    def __init__(self, name: str, behavior: str = "ok", reply: str = "canned reply"):
        self.name = name
        self.behavior = behavior
        self.reply = reply
        self.calls = 0

    async def generate(self, messages, model):
        self.calls += 1
        if self.behavior == "ok":
            return self.reply
        if self.behavior == "unavailable":
            raise ProviderUnavailableError(f"{self.name} unavailable")
        if self.behavior == "error":
            raise ValueError(f"{self.name} bad request")
        raise AssertionError(f"unknown behavior: {self.behavior}")


@pytest.fixture
def sqlite_engine():
    """A fresh in-memory SQLite engine with all tables created.

    Uses a single shared connection (StaticPool) so the same in-memory
    database is visible across every session opened from this engine -
    otherwise each connection would see a separate, empty database.
    """
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
    """Two fake providers (gemini, groq), both healthy by default.

    Tests can mutate `.behavior` on either one before making a request to
    simulate an outage.
    """
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
