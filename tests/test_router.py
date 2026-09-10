"""Tests for the model router: explicit routing, auto fallback, and errors.

Uses only fake providers (see conftest.py) - no real API keys or network
calls involved.
"""
import pytest

from app.providers.base import ProviderUnavailableError
from app.router.model_router import ModelRouter, NoProviderAvailableError
from tests.conftest import FakeProvider

MESSAGES = [{"role": "user", "content": "hi"}]


async def test_explicit_provider_routes_directly(fake_providers):
    router = ModelRouter(fake_providers)

    result = await router.route("gemini", MESSAGES)

    assert result.provider == "gemini"
    assert result.content == "hello from gemini"
    assert fake_providers["gemini"].calls == 1
    assert fake_providers["groq"].calls == 0


async def test_explicit_provider_selects_the_right_one(fake_providers):
    router = ModelRouter(fake_providers)

    result = await router.route("groq", MESSAGES)

    assert result.provider == "groq"
    assert result.content == "hello from groq"


async def test_unknown_provider_raises_value_error(fake_providers):
    router = ModelRouter(fake_providers)

    with pytest.raises(ValueError):
        await router.route("not-a-real-provider", MESSAGES)


async def test_explicit_provider_failure_has_no_fallback():
    """An explicitly requested provider failing should not try another one."""
    down = FakeProvider("gemini", behavior="unavailable")
    healthy = FakeProvider("groq", behavior="ok")
    router = ModelRouter({"gemini": down, "groq": healthy})

    with pytest.raises(ProviderUnavailableError):
        await router.route("gemini", MESSAGES)

    assert healthy.calls == 0


async def test_explicit_provider_client_error_propagates_unchanged():
    """A normal client error (not availability-related) is never masked by fallback."""
    bad = FakeProvider("gemini", behavior="error")
    router = ModelRouter({"gemini": bad, "groq": FakeProvider("groq")})

    with pytest.raises(ValueError):
        await router.route("gemini", MESSAGES)


async def test_auto_uses_healthy_primary_without_calling_fallback(fake_providers):
    router = ModelRouter(fake_providers)

    result = await router.route("auto", MESSAGES)

    assert result.provider == "gemini"
    assert fake_providers["gemini"].calls == 1
    assert fake_providers["groq"].calls == 0


async def test_auto_falls_back_when_primary_is_unavailable():
    down = FakeProvider("gemini", behavior="unavailable")
    healthy = FakeProvider("groq", behavior="ok", reply="fallback reply")
    router = ModelRouter({"gemini": down, "groq": healthy})

    result = await router.route("auto", MESSAGES)

    assert result.provider == "groq"
    assert result.content == "fallback reply"
    assert down.calls == 1
    assert healthy.calls == 1


async def test_auto_raises_when_every_provider_is_unavailable():
    router = ModelRouter(
        {
            "gemini": FakeProvider("gemini", behavior="unavailable"),
            "groq": FakeProvider("groq", behavior="unavailable"),
        }
    )

    with pytest.raises(NoProviderAvailableError):
        await router.route("auto", MESSAGES)
