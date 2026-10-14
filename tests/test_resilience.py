"""Router resilience: retries, timeouts, deadline, circuit breaker, routing
strategies, malformed output, and stream cancellation. Fake providers and
a fake clock - no network, no real sleeping."""
import asyncio

import httpx
import pytest

from app.gateway.config import PriceConfig
from app.gateway.health import BreakerState, ProviderHealth
from app.gateway.retry import RetryPolicy
from app.providers.base import (
    GenerationResult,
    LLMProvider,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    StreamEvent,
)
from app.providers.mock import MockProvider
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.router.model_router import ModelRouter, NoProviderAvailableError, UnknownModelError
from tests.conftest import FakeProvider

MESSAGES = [{"role": "user", "content": "hi"}]


class FlakyProvider(LLMProvider):
    """Raises `error` for the first `failures` calls, then succeeds."""

    def __init__(self, name, failures, error=ProviderUnavailableError):
        self.name = name
        self.failures = failures
        self.error = error
        self.calls = 0

    async def generate(self, messages, model, params=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error(f"{self.name} failure #{self.calls}")
        return GenerationResult(content=f"ok from {self.name}")


class HangingProvider(LLMProvider):
    def __init__(self, name):
        self.name = name
        self.calls = 0

    async def generate(self, messages, model, params=None):
        self.calls += 1
        await asyncio.sleep(3600)

    async def generate_stream(self, messages, model, params=None):
        self.calls += 1
        await asyncio.sleep(3600)
        yield StreamEvent("never")


class TrackingStreamProvider(LLMProvider):
    """Streams forever until closed; records whether it was closed."""

    def __init__(self, name):
        self.name = name
        self.closed = False

    async def generate(self, messages, model, params=None):
        raise AssertionError("not used")

    async def generate_stream(self, messages, model, params=None):
        try:
            while True:
                yield StreamEvent("tick ")
                await asyncio.sleep(0)
        finally:
            self.closed = True


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


async def _no_sleep(_seconds):
    return None


async def _collect(stream):
    return [chunk async for chunk in stream]


# ---- retries -----------------------------------------------------------------


async def test_retries_same_provider_before_falling_back():
    flaky = FlakyProvider("a", failures=1)
    backup = FakeProvider("b")
    router = ModelRouter({"a": flaky, "b": backup}, retry=RetryPolicy(max_retries=1), sleep=_no_sleep)

    result = await router.route("auto", MESSAGES)

    assert result.provider == "a"
    assert result.fallback is False
    assert flaky.calls == 2
    assert backup.calls == 0


async def test_retries_are_bounded_then_fall_back():
    flaky = FlakyProvider("a", failures=10)
    backup = FakeProvider("b", reply="from backup")
    router = ModelRouter({"a": flaky, "b": backup}, retry=RetryPolicy(max_retries=2), sleep=_no_sleep)

    result = await router.route("auto", MESSAGES)

    assert flaky.calls == 3  # 1 try + 2 retries, never more
    assert result.provider == "b"
    assert result.fallback is True


async def test_rate_limited_provider_is_not_retried_but_falls_back_immediately():
    limited = FlakyProvider("a", failures=10, error=ProviderRateLimitedError)
    backup = FakeProvider("b")
    router = ModelRouter({"a": limited, "b": backup}, retry=RetryPolicy(max_retries=3), sleep=_no_sleep)

    result = await router.route("auto", MESSAGES)

    assert limited.calls == 1
    assert result.provider == "b"


async def test_client_errors_are_never_retried():
    bad = FakeProvider("a", behavior="error")
    router = ModelRouter({"a": bad, "b": FakeProvider("b")}, retry=RetryPolicy(max_retries=3), sleep=_no_sleep)

    with pytest.raises(ValueError):
        await router.route("auto", MESSAGES)

    assert bad.calls == 1


def test_backoff_delay_is_jittered_and_capped():
    policy = RetryPolicy(max_retries=5, backoff_base_seconds=0.5, backoff_max_seconds=2.0)
    for retry_number in range(6):
        for _ in range(50):
            assert 0 <= policy.delay(retry_number) <= min(2.0, 0.5 * 2**retry_number)


# ---- timeouts and deadline -----------------------------------------------------


async def test_hanging_provider_times_out_and_falls_back():
    hanging = HangingProvider("a")
    backup = FakeProvider("b", reply="from backup")
    router = ModelRouter({"a": hanging, "b": backup}, provider_timeouts={"a": 0.05, "b": 1.0})

    result = await asyncio.wait_for(router.route("auto", MESSAGES), timeout=2)

    assert result.provider == "b"
    assert router.health["a"].last_error.startswith("a timed out")


async def test_stream_first_token_timeout_falls_back():
    hanging = HangingProvider("a")
    backup = FakeProvider("b", reply="from backup")
    router = ModelRouter({"a": hanging, "b": backup}, provider_timeouts={"a": 0.05})

    chunks = await asyncio.wait_for(_collect(router.route_stream("auto", MESSAGES)), timeout=2)

    assert "".join(c.delta for c in chunks) == "from backup"
    assert chunks[-1].fallback is True


async def test_total_deadline_bounds_the_whole_request():
    router = ModelRouter(
        {"a": HangingProvider("a"), "b": HangingProvider("b")},
        request_timeout=0.1,
        retry=RetryPolicy(max_retries=3),
    )

    with pytest.raises(NoProviderAvailableError):
        await asyncio.wait_for(router.route("auto", MESSAGES), timeout=2)


# ---- circuit breaker -------------------------------------------------------------


def test_breaker_state_machine():
    clock = FakeClock()
    health = ProviderHealth("a", failure_threshold=2, cooldown_seconds=30, clock=clock)

    health.record_failure("x")
    assert health.state is BreakerState.CLOSED
    health.record_failure("x")
    assert health.state is BreakerState.OPEN
    assert health.allow_request() is False

    clock.now += 30
    assert health.allow_request() is True  # the single half-open trial
    assert health.state is BreakerState.HALF_OPEN
    assert health.allow_request() is False  # no second concurrent trial

    health.record_failure("still down")
    assert health.state is BreakerState.OPEN

    clock.now += 30
    assert health.allow_request() is True
    health.record_success(0.1)
    assert health.state is BreakerState.CLOSED
    assert health.consecutive_failures == 0


async def test_open_circuit_removes_provider_from_routing_until_cooldown():
    clock = FakeClock()
    down = FakeProvider("a", behavior="unavailable")
    backup = FakeProvider("b")
    router = ModelRouter({"a": down, "b": backup}, failure_threshold=2, cooldown_seconds=30, clock=clock)

    for _ in range(2):
        await router.route("auto", MESSAGES)
    assert router.health["a"].state is BreakerState.OPEN

    for _ in range(3):
        result = await router.route("auto", MESSAGES)
        assert result.provider == "b"
    assert down.calls == 2  # not called while open

    clock.now += 30
    down.behavior = "ok"
    result = await router.route("auto", MESSAGES)
    assert result.provider == "a"
    assert router.health["a"].state is BreakerState.CLOSED


async def test_explicit_provider_with_open_circuit_fails_fast():
    down = FakeProvider("a", behavior="unavailable")
    router = ModelRouter({"a": down}, failure_threshold=1)

    with pytest.raises(ProviderUnavailableError):
        await router.route("a", MESSAGES)
    with pytest.raises(ProviderUnavailableError, match="circuit is open"):
        await router.route("a", MESSAGES)
    assert down.calls == 1


# ---- registry and routing strategies -----------------------------------------------


def test_resolve_alias_provider_and_explicit_model():
    router = ModelRouter(
        {"a": MockProvider("a", default_model="a-1"), "b": MockProvider("b", default_model="b-1")},
        {"smart": ["b/b-large", "a"]},
    )

    assert [tuple(t) for t in router.resolve("smart")] == [("b", "b-large"), ("a", "a-1")]
    assert [tuple(t) for t in router.resolve("a")] == [("a", "a-1")]
    assert [tuple(t) for t in router.resolve("b/some/nested-model")] == [("b", "some/nested-model")]
    with pytest.raises(UnknownModelError):
        router.resolve("gpt-4o")


def test_alias_referencing_unknown_provider_is_rejected_at_construction():
    with pytest.raises(ValueError):
        ModelRouter({"a": MockProvider("a")}, {"smart": ["nope"]})


async def test_cost_strategy_prefers_cheaper_target():
    router = ModelRouter(
        {"a": MockProvider("a"), "b": MockProvider("b")},
        {"auto": ["a/m", "b/m"]},
        strategy="cost",
        pricing={
            "a/m": PriceConfig(input_per_million=5, output_per_million=15),
            "b/m": PriceConfig(input_per_million=0.1, output_per_million=0.3),
        },
    )

    assert (await router.route("auto", MESSAGES)).provider == "b"


async def test_latency_strategy_prefers_faster_provider():
    router = ModelRouter({"a": MockProvider("a"), "b": MockProvider("b")}, strategy="latency")
    router.health["a"].record_success(2.0)
    router.health["b"].record_success(0.2)

    assert (await router.route("auto", MESSAGES)).provider == "b"


async def test_load_strategy_prefers_less_busy_provider():
    router = ModelRouter({"a": MockProvider("a"), "b": MockProvider("b")}, strategy="load")
    router.health["a"].in_flight = 5

    assert (await router.route("auto", MESSAGES)).provider == "b"


async def test_embed_skips_targets_without_embedding_support():
    router = ModelRouter({"chat_only": FakeProvider("chat_only"), "m": MockProvider("m")})

    result = await router.embed("auto", ["hello", "world"])

    assert result.provider == "m"
    assert len(result.vectors) == 2


async def test_embed_on_model_without_any_support_is_a_client_error():
    router = ModelRouter({"chat_only": FakeProvider("chat_only")})

    with pytest.raises(UnknownModelError):
        await router.embed("chat_only", ["x"])


# ---- malformed provider output ---------------------------------------------------


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json={"unexpected": "shape"}),
        httpx.Response(200, json=["not", "an", "object"]),
    ],
)
async def test_malformed_provider_output_is_an_availability_failure_and_falls_back(response):
    provider = OpenAICompatibleProvider("weird", "http://weird.test/v1", "k", "m")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))
    router = ModelRouter({"weird": provider, "b": FakeProvider("b", reply="sane")})

    result = await router.route("auto", MESSAGES)

    assert result.content == "sane"
    assert router.health["weird"].total_failures == 1


async def test_malformed_stream_event_before_first_token_falls_back():
    provider = OpenAICompatibleProvider("weird", "http://weird.test/v1", "k", "m")
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="data: {broken\n\n"))
    )
    router = ModelRouter({"weird": provider, "b": FakeProvider("b", reply="sane")})

    chunks = await _collect(router.route_stream("auto", MESSAGES))

    assert "".join(c.delta for c in chunks) == "sane"


# ---- stream cancellation -----------------------------------------------------------


async def test_closing_the_stream_early_closes_the_upstream_stream():
    upstream = TrackingStreamProvider("a")
    router = ModelRouter({"a": upstream})

    stream = router.route_stream("a", MESSAGES)
    first = await stream.__anext__()
    await stream.aclose()  # what a client disconnect does

    assert first.delta == "tick "
    assert upstream.closed is True
    assert router.health["a"].in_flight == 0


async def test_disconnect_during_half_open_trial_does_not_wedge_the_breaker():
    clock = FakeClock()
    upstream = TrackingStreamProvider("a")
    router = ModelRouter({"a": upstream}, failure_threshold=1, cooldown_seconds=10, clock=clock)
    router.health["a"].record_failure("down")
    clock.now += 10

    stream = router.route_stream("a", MESSAGES)
    await stream.__anext__()  # takes the half-open trial slot
    await stream.aclose()

    assert router.health["a"].allow_request() is True  # slot was released
