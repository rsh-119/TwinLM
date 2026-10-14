"""Router tests: explicit routing, auto fallback, and errors. Fake providers
only - no real API keys or network calls."""
import pytest

from app.providers.base import ProviderUnavailableError, TokenUsage
from app.router.model_router import ModelRouter, NoProviderAvailableError
from tests.conftest import FakeProvider

MESSAGES = [{"role": "user", "content": "hi"}]


async def _collect(stream):
    return [chunk async for chunk in stream]


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


async def test_explicit_provider_returns_reported_token_usage(fake_providers):
    fake_providers["gemini"].usage = TokenUsage(prompt_tokens=12, completion_tokens=8, total_tokens=20)
    router = ModelRouter(fake_providers)

    result = await router.route("gemini", MESSAGES)

    assert result.usage == TokenUsage(prompt_tokens=12, completion_tokens=8, total_tokens=20)


async def test_explicit_provider_usage_is_none_when_not_reported(fake_providers):
    router = ModelRouter(fake_providers)

    result = await router.route("gemini", MESSAGES)

    assert result.usage is None


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


# ---- streaming: route_stream ----


async def test_stream_explicit_provider_yields_chunks_then_a_done_marker(fake_providers):
    fake_providers["gemini"].chunks = ["hello ", "from ", "gemini"]
    router = ModelRouter(fake_providers)

    chunks = await _collect(router.route_stream("gemini", MESSAGES))

    assert [c.delta for c in chunks] == ["hello ", "from ", "gemini", ""]
    assert [c.done for c in chunks] == [False, False, False, True]
    assert all(c.provider == "gemini" for c in chunks)
    assert fake_providers["groq"].stream_calls == 0


async def test_stream_explicit_provider_reports_usage_only_on_the_done_chunk(fake_providers):
    fake_providers["gemini"].chunks = ["hello ", "from ", "gemini"]
    fake_providers["gemini"].usage = TokenUsage(prompt_tokens=12, completion_tokens=8, total_tokens=20)
    router = ModelRouter(fake_providers)

    chunks = await _collect(router.route_stream("gemini", MESSAGES))

    assert [c.usage for c in chunks[:-1]] == [None, None, None]
    assert chunks[-1].done is True
    assert chunks[-1].usage == TokenUsage(prompt_tokens=12, completion_tokens=8, total_tokens=20)


async def test_stream_auto_reports_usage_from_whichever_provider_answered(fake_providers):
    fake_providers["gemini"].behavior = "unavailable"
    fake_providers["groq"].chunks = ["fallback ", "reply"]
    fake_providers["groq"].usage = TokenUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8)
    router = ModelRouter(fake_providers)

    chunks = await _collect(router.route_stream("auto", MESSAGES))

    assert chunks[-1].usage == TokenUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8)


async def test_stream_unknown_provider_raises_value_error(fake_providers):
    router = ModelRouter(fake_providers)

    with pytest.raises(ValueError):
        await _collect(router.route_stream("not-a-real-provider", MESSAGES))


async def test_stream_explicit_provider_failure_has_no_fallback():
    down = FakeProvider("gemini", behavior="unavailable")
    healthy = FakeProvider("groq", behavior="ok")
    router = ModelRouter({"gemini": down, "groq": healthy})

    with pytest.raises(ProviderUnavailableError):
        await _collect(router.route_stream("gemini", MESSAGES))

    assert healthy.stream_calls == 0


async def test_stream_auto_uses_healthy_primary_without_calling_fallback(fake_providers):
    router = ModelRouter(fake_providers)

    chunks = await _collect(router.route_stream("auto", MESSAGES))

    assert "".join(c.delta for c in chunks) == "hello from gemini"
    assert fake_providers["gemini"].stream_calls == 1
    assert fake_providers["groq"].stream_calls == 0


async def test_stream_auto_falls_back_before_the_first_chunk(fake_providers):
    fake_providers["gemini"].behavior = "unavailable"
    fake_providers["groq"].chunks = ["fallback ", "reply"]
    router = ModelRouter(fake_providers)

    chunks = await _collect(router.route_stream("auto", MESSAGES))

    assert "".join(c.delta for c in chunks) == "fallback reply"
    assert all(c.provider == "groq" for c in chunks)
    assert fake_providers["gemini"].stream_calls == 1
    assert fake_providers["groq"].stream_calls == 1


async def test_stream_auto_raises_when_every_provider_is_unavailable():
    router = ModelRouter(
        {
            "gemini": FakeProvider("gemini", behavior="unavailable"),
            "groq": FakeProvider("groq", behavior="unavailable"),
        }
    )

    with pytest.raises(NoProviderAvailableError):
        await _collect(router.route_stream("auto", MESSAGES))


async def test_stream_auto_does_not_retry_or_swallow_an_unexpected_error():
    """If the fallback provider itself fails with something that isn't
    ProviderUnavailableError, that error should propagate as-is - the
    router shouldn't loop forever or mask it as NoProviderAvailableError."""
    down = FakeProvider("gemini", behavior="unavailable")
    broken = FakeProvider("groq", behavior="crash")
    router = ModelRouter({"gemini": down, "groq": broken})

    with pytest.raises(RuntimeError):
        await _collect(router.route_stream("auto", MESSAGES))


async def test_stream_auto_does_not_fall_back_once_a_chunk_has_been_sent():
    """Once the primary has started streaming, a mid-response failure ends
    the stream (with a note appended) instead of switching providers -
    bytes may already be with the client."""
    flaky = FakeProvider("gemini", behavior="unavailable_mid_stream", chunks=["partial "])
    healthy = FakeProvider("groq", behavior="ok", reply="should not be used")
    router = ModelRouter({"gemini": flaky, "groq": healthy})

    chunks = await _collect(router.route_stream("auto", MESSAGES))

    assert chunks[0].delta == "partial "
    assert "interrupted" in "".join(c.delta for c in chunks)
    assert chunks[-1].done is True
    assert chunks[-1].interrupted is True
    assert chunks[-1].finish_reason == "error"
    assert all(c.provider == "gemini" for c in chunks)
    assert healthy.stream_calls == 0


async def test_stream_output_cap_marks_reply_interrupted(fake_providers):
    fake_providers["gemini"].chunks = ["oversized"]
    router = ModelRouter(fake_providers, max_stream_output_chars=4)
    chunks = await _collect(router.route_stream("gemini", MESSAGES))
    assert chunks[0].delta == "over"
    assert chunks[-1].interrupted is True
    assert chunks[-1].finish_reason == "error"
