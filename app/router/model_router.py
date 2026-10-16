"""Resolves a requested `model` to ordered *model groups* of deployments
and runs the request against them with timeouts, retries, circuit breaking
and fallback.

A deployment is one (provider endpoint+credential, model) pair with its
own id and capability set. A model group is several deployments serving
the same model. `model` can be:
- an alias from gateway.toml (`auto`, `smart`, ...): its groups in order;
- a deployment id from `[deployments.<id>]`: exactly that, no fallback;
- a provider name (`gemini`): that provider's default model, no fallback;
- `provider/model` (`groq/openai/gpt-oss-20b`): exactly that, no fallback.

Per deployment: skip it if its provider's circuit is open; otherwise try it
with a per-attempt timeout, retrying availability failures (not 429s) with
jittered backoff. Then try the next deployment of the *same* group (not a
fallback), then the next group (a fallback: a different model).
Deployments lacking a capability the request needs (tools, vision,
embeddings) are never tried. Client errors and unexpected exceptions
propagate unchanged. A total deadline bounds the whole thing.
"""
import asyncio
import logging
import math
import os
import time
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
    Union,
)

import anyio

from app.config import Settings, get_settings
from app.gateway.config import GatewayConfig, PriceConfig, ProviderConfig, get_gateway_config
from app.gateway.health import BreakerState, ProviderHealth
from app.gateway.pricing import price_key
from app.gateway.retry import RetryPolicy
from app.observability import (
    ERRORS,
    FALLBACKS,
    PROVIDER_HEALTH,
    PROVIDER_LATENCY,
    RETRIES,
    TTFT,
)
from app.providers.base import (
    GenerationParams,
    LLMProvider,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    TokenUsage,
)
from app.providers.gemini import DEFAULT_GEMINI_MODEL, GEMINI_API_BASE, GeminiProvider
from app.providers.groq import DEFAULT_GROQ_MODEL
from app.providers.mock import MockProvider
from app.providers.openai_compatible import OpenAICompatibleProvider

logger = logging.getLogger("twinlm.router")

T = TypeVar("T")

_HEALTH_GAUGE = {BreakerState.CLOSED: 1.0, BreakerState.HALF_OPEN: 0.5, BreakerState.OPEN: 0.0}


class NoProviderAvailableError(Exception):
    """Every target was unavailable (or the deadline ran out)."""


class UnknownModelError(ValueError):
    """`model` is not an alias, provider, or provider/model."""


class Target(NamedTuple):
    provider: str
    model: str


class Deployment(NamedTuple):
    """A routable (provider, model) with an identity separate from both.
    `capabilities` None = unknown (a router built without config, e.g. in
    tests): never gated."""

    id: str
    target: Target
    capabilities: Optional[FrozenSet[str]] = None

    def supports(self, required: FrozenSet[str]) -> bool:
        return self.capabilities is None or required <= self.capabilities


# What each adapter type can send. Narrow it per provider or deployment in
# gateway.toml when a model cannot (no vision, no embeddings endpoint).
DEFAULT_CAPABILITIES = {
    "gemini": frozenset({"chat", "tools", "vision", "embeddings"}),
    "openai_compatible": frozenset({"chat", "tools", "vision", "embeddings"}),
    "mock": frozenset({"chat", "embeddings"}),
}


def required_capabilities(messages: Iterable[Dict[str, Any]], params: Optional[Dict[str, Any]]) -> FrozenSet[str]:
    needed = {"chat"}
    if params and params.get("tools"):
        needed.add("tools")
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(part.get("type") == "image_url" for part in content):
            needed.add("vision")
    return frozenset(needed)


GroupSpec = Union[str, Sequence[str]]


class RouteResult(NamedTuple):
    provider: str
    model: str
    content: str
    usage: Optional[TokenUsage] = None
    fallback: bool = False
    tool_calls: Optional[List[Dict[str, Any]]] = None
    finish_reason: Optional[str] = None
    deployment: Optional[str] = None


class RouteChunk(NamedTuple):
    """One piece of a streamed reply. `provider`/`model` are set from the
    first chunk onward, so a caller can show "gemini is answering" before
    the reply is even finished. `done=True` marks the last chunk for a
    turn - `delta` may or may not be empty on that one, and `usage`,
    `fallback` and `ttft` (seconds to first token) are only ever set on
    that last chunk, as is `finish_reason`. `tool_calls` carries OpenAI
    streamed tool-call deltas on whichever chunk they arrived."""

    provider: str
    model: str
    delta: str
    done: bool = False
    usage: Optional[TokenUsage] = None
    fallback: bool = False
    ttft: Optional[float] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    finish_reason: Optional[str] = None
    interrupted: bool = False
    deployment: Optional[str] = None


class EmbedResult(NamedTuple):
    provider: str
    model: str
    vectors: List[List[float]]
    usage: Optional[TokenUsage] = None
    fallback: bool = False
    deployment: Optional[str] = None


class ModelRouter:
    # Default model per provider, for providers that don't declare
    # `default_model` themselves (e.g. test fakes).
    DEFAULT_MODELS = {
        "gemini": DEFAULT_GEMINI_MODEL,
        "groq": DEFAULT_GROQ_MODEL,
    }

    def __init__(
        self,
        providers: Dict[str, LLMProvider],
        aliases: Optional[Dict[str, List[GroupSpec]]] = None,
        *,
        deployments: Optional[Dict[str, Tuple[str, str, Optional[FrozenSet[str]]]]] = None,
        provider_capabilities: Optional[Dict[str, FrozenSet[str]]] = None,
        retry: RetryPolicy = RetryPolicy(),
        strategy: str = "priority",
        pricing: Optional[Dict[str, PriceConfig]] = None,
        request_timeout: Optional[float] = None,
        max_stream_output_chars: int = 250_000,
        provider_timeouts: Optional[Dict[str, float]] = None,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Only `providers` is required: with no `aliases`, `auto` tries
        every provider in dict order, and there are no retries or
        timeouts - the original behavior. An alias entry that is a string
        is a one-deployment group; a list of strings is one model group.
        `deployments` maps id -> (provider, model, capabilities or None)."""
        self._providers = providers
        self._provider_capabilities = provider_capabilities or {}
        self._deployments: Dict[str, Deployment] = {}
        for deployment_id, (provider, model, caps) in (deployments or {}).items():
            if provider not in providers:
                raise ValueError(f"deployment {deployment_id!r} names an unknown provider")
            self._deployments[deployment_id] = Deployment(
                deployment_id, Target(provider, model), caps or self._provider_capabilities.get(provider)
            )
        self._retry = retry
        self._strategy = strategy
        self._pricing = pricing or {}
        self._request_timeout = request_timeout
        self._max_stream_output_chars = max_stream_output_chars
        self._provider_timeouts = provider_timeouts or {}
        self._clock = clock
        self._sleep = sleep
        self._aliases: Dict[str, List[List[Deployment]]] = (
            {
                name: [
                    [self._parse_target(spec)] if isinstance(spec, str) else [self._parse_target(s) for s in spec]
                    for spec in specs
                ]
                for name, specs in aliases.items()
            }
            if aliases is not None
            else {"auto": [[self._parse_target(name)] for name in providers]}
        )
        self.health: Dict[str, ProviderHealth] = {
            name: ProviderHealth(name, failure_threshold, cooldown_seconds, clock) for name in providers
        }
        for name in providers:
            self._publish_health(name)

    # ---- registry ----------------------------------------------------------

    @property
    def model_ids(self) -> List[str]:
        """Every alias, deployment id and provider name `route` accepts as
        `model` (any `provider/model` is accepted too)."""
        return list(dict.fromkeys([*self._aliases, *self._deployments, *self._providers]))

    def capabilities(self, model: str) -> Optional[List[str]]:
        """Union over the model's deployments - what at least one supports.
        None when any deployment's capabilities are unknown."""
        deployments = [d for group in self._groups(model) for d in group]
        if any(d.capabilities is None for d in deployments):
            return None
        return sorted(set().union(*(d.capabilities for d in deployments)))

    def describe_models(self) -> List[dict]:
        out = []
        for alias, groups in self._aliases.items():
            out.append({
                "id": alias,
                "type": "alias",
                "groups": [[self._describe(d) for d in group] for group in groups],
                "targets": [self._describe(d) for group in groups for d in group],
            })
        for deployment_id, deployment in self._deployments.items():
            out.append({"id": deployment_id, "type": "deployment", "targets": [self._describe(deployment)]})
        for name in self._providers:
            out.append({"id": name, "type": "provider", "targets": [self._describe(self._parse_target(name))]})
        return out

    def _describe(self, deployment: Deployment) -> dict:
        price = self._pricing.get(price_key(*deployment.target))
        return {
            "deployment": deployment.id,
            "provider": deployment.target.provider,
            "model": deployment.target.model,
            "capabilities": sorted(deployment.capabilities) if deployment.capabilities is not None else None,
            "pricing": price.model_dump() if price else None,
        }

    def health_snapshot(self) -> List[dict]:
        return [health.snapshot() for health in self.health.values()]

    def resolve(self, model: str) -> List[Target]:
        return [d.target for group in self._groups(model) for d in group]

    def _groups(self, model: str) -> List[List[Deployment]]:
        if model in self._aliases:
            return [list(group) for group in self._aliases[model]]
        if model in self._deployments:
            return [[self._deployments[model]]]
        if model.partition("/")[0] in self._providers:
            return [[self._parse_target(model)]]
        raise UnknownModelError(f"unknown model or provider: {model!r}")

    def _plan(self, model: str, required: FrozenSet[str]) -> List[Tuple[Deployment, bool]]:
        """Deployments in attempt order, each with whether it is a fallback
        (outside the first group that can serve the request)."""
        groups = [[d for d in group if d.supports(required)] for group in self._groups(model)]
        groups = [group for group in groups if group]
        if not groups:
            missing = ", ".join(sorted(required - {"chat"})) or ", ".join(sorted(required))
            raise UnknownModelError(f"model {model!r} has no deployment supporting: {missing}")
        return [(d, index > 0) for index, group in enumerate(self._order(groups)) for d in group]

    def _default_model(self, provider: str) -> str:
        declared = getattr(self._providers[provider], "default_model", None)
        return declared or self.DEFAULT_MODELS.get(provider, provider)

    def _parse_target(self, spec: str) -> Deployment:
        if spec in self._deployments:
            return self._deployments[spec]
        provider, _, model = spec.partition("/")
        if provider not in self._providers:
            raise ValueError(f"model alias target {spec!r} names an unknown provider")
        target = Target(provider, model or self._default_model(provider))
        return Deployment(f"{target.provider}/{target.model}", target, self._provider_capabilities.get(provider))

    # ---- routing policy ----------------------------------------------------

    def _order(self, groups: List[List[Deployment]]) -> List[List[Deployment]]:
        """Reorder deployments within each group, then groups by their best
        member (stable, so config order breaks ties); `priority` keeps the
        configured order. Health filtering happens per attempt via
        `allow_request`."""
        if self._strategy == "latency":
            # unmeasured providers sort first, so they get measured
            key: Callable[[Deployment], float] = lambda d: self.health[d.target.provider].latency_ewma or 0.0  # noqa: E731
        elif self._strategy == "cost":
            key = self._unit_price
        elif self._strategy == "load":
            key = lambda d: self.health[d.target.provider].in_flight  # noqa: E731
        else:
            return groups
        groups = [sorted(group, key=key) for group in groups]
        return sorted(groups, key=lambda group: key(group[0]))

    def _unit_price(self, deployment: Deployment) -> float:
        price = self._pricing.get(price_key(*deployment.target))
        return price.input_per_million + price.output_per_million if price else math.inf

    def _deadline(self) -> Optional[float]:
        return self._clock() + self._request_timeout if self._request_timeout else None

    def _attempt_timeout(self, provider: str, deadline: Optional[float]) -> Optional[float]:
        limits = [self._provider_timeouts[provider]] if provider in self._provider_timeouts else []
        if deadline is not None:
            limits.append(deadline - self._clock())
        return min(limits) if limits else None

    # ---- bookkeeping -------------------------------------------------------

    def _publish_health(self, provider: str) -> None:
        PROVIDER_HEALTH.labels(provider).set(_HEALTH_GAUGE[self.health[provider].state])

    def _record_failure(self, provider: str, error: ProviderUnavailableError) -> None:
        if isinstance(error, ProviderRateLimitedError):
            reason = "rate_limited"
        elif "timed out" in str(error):
            reason = "timeout"
        else:
            reason = "unavailable"
        ERRORS.labels(provider, reason).inc()
        self.health[provider].record_failure(str(error))
        self._publish_health(provider)
        logger.warning("provider attempt failed: provider=%s reason=%s error=%s", provider, reason, error)

    def _record_success(self, provider: str, latency: float) -> None:
        PROVIDER_LATENCY.labels(provider).observe(latency)
        self.health[provider].record_success(latency)
        self._publish_health(provider)

    def _circuit_open(self, provider: str) -> ProviderUnavailableError:
        ERRORS.labels(provider, "circuit_open").inc()
        return ProviderUnavailableError(f"{provider} circuit is open")

    @staticmethod
    def _timed_out(provider: str, timeout: Optional[float]) -> ProviderUnavailableError:
        return ProviderUnavailableError(f"{provider} timed out after {timeout:.1f}s")

    async def _backoff(self, provider: str, retry_number: int, deadline: Optional[float]) -> None:
        delay = self._retry.delay(retry_number)
        if deadline is not None:
            delay = min(delay, max(0.0, deadline - self._clock()))
        RETRIES.labels(provider).inc()
        await self._sleep(delay)

    def _should_retry(self, error: ProviderUnavailableError, attempt: int) -> bool:
        return not isinstance(error, ProviderRateLimitedError) and attempt < self._retry.max_retries

    @staticmethod
    def _exhausted(targets: List[Any], last_error: Optional[Exception], unsupported: int) -> Exception:
        if unsupported == len(targets):
            return UnknownModelError("no target of this model supports the requested operation")
        if len(targets) == 1 and isinstance(last_error, ProviderUnavailableError):
            return last_error  # explicit provider: surface its own error
        error = NoProviderAvailableError(
            "all providers unavailable" if last_error else "request deadline exceeded"
        )
        error.__cause__ = last_error
        return error

    @staticmethod
    async def _close(stream: AsyncIterator) -> None:
        # Shielded: on client disconnect this runs inside a cancelled
        # scope, and the upstream request must still be torn down.
        with anyio.CancelScope(shield=True):
            try:
                await stream.aclose()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - best-effort cleanup
                logger.debug("error closing provider stream", exc_info=True)

    # ---- non-streaming -----------------------------------------------------

    async def _call(
        self, model: str, required: FrozenSet[str], call: Callable[[LLMProvider, str], Awaitable[T]]
    ) -> Tuple[Deployment, T, bool]:
        targets = self._plan(model, required)
        deadline = self._deadline()
        last_error: Optional[Exception] = None
        unsupported = 0

        for deployment, fallback in targets:
            target = deployment.target
            provider = self._providers[target.provider]
            health = self.health[target.provider]
            for attempt in range(self._retry.max_retries + 1):
                if not health.allow_request():
                    last_error = self._circuit_open(target.provider)
                    break
                timeout = self._attempt_timeout(target.provider, deadline)
                if timeout is not None and timeout <= 0:
                    raise self._exhausted(targets, last_error, unsupported)

                started = self._clock()
                health.in_flight += 1
                try:
                    async with asyncio.timeout(timeout):
                        value = await call(provider, target.model)
                except NotImplementedError:
                    health.record_neutral()
                    unsupported += 1
                    break
                except (ProviderUnavailableError, TimeoutError) as exc:
                    error = exc if isinstance(exc, ProviderUnavailableError) else self._timed_out(target.provider, timeout)
                    self._record_failure(target.provider, error)
                    last_error = error
                    if not self._should_retry(error, attempt):
                        break
                    await self._backoff(target.provider, attempt, deadline)
                    continue
                except BaseException:
                    health.record_neutral()
                    raise
                finally:
                    health.in_flight -= 1

                self._record_success(target.provider, self._clock() - started)
                if fallback:
                    FALLBACKS.labels(target.provider).inc()
                return deployment, value, fallback

        raise self._exhausted(targets, last_error, unsupported)

    async def route(
        self, model: str, messages: List[Dict[str, str]], params: Optional[GenerationParams] = None
    ) -> RouteResult:
        deployment, result, fallback = await self._call(
            model, required_capabilities(messages, params),
            lambda provider, name: provider.generate(messages, name, params),
        )
        return RouteResult(
            deployment.target.provider, deployment.target.model, result.content, result.usage, fallback,
            result.tool_calls, result.finish_reason, deployment.id,
        )

    async def embed(self, model: str, texts: List[str]) -> EmbedResult:
        deployment, result, fallback = await self._call(
            model, frozenset({"embeddings"}), lambda provider, name: provider.embed(texts, name)
        )
        target = deployment.target
        return EmbedResult(target.provider, target.model, result.vectors, result.usage, fallback, deployment.id)

    # ---- streaming ---------------------------------------------------------

    async def route_stream(
        self, model: str, messages: List[Dict[str, str]], params: Optional[GenerationParams] = None
    ) -> AsyncIterator[RouteChunk]:
        """Streaming counterpart to `route`. Retries and fallback can only
        happen *before* the first chunk goes out - after that it's on its
        way to an HTTP client and can't be recalled, so a provider failing
        mid-stream ends the stream with a short note appended instead.

        The total deadline bounds time-to-first-token; after that each
        chunk must arrive within the provider's timeout (idle timeout),
        so long replies aren't cut off by the deadline.
        """
        targets = self._plan(model, required_capabilities(messages, params))
        deadline = self._deadline()
        last_error: Optional[Exception] = None

        for deployment, fallback in targets:
            target = deployment.target
            provider = self._providers[target.provider]
            health = self.health[target.provider]
            for attempt in range(self._retry.max_retries + 1):
                if not health.allow_request():
                    last_error = self._circuit_open(target.provider)
                    break
                timeout = self._attempt_timeout(target.provider, deadline)
                if timeout is not None and timeout <= 0:
                    raise self._exhausted(targets, last_error, 0)

                started = self._clock()
                health.in_flight += 1
                stream = provider.generate_stream(messages, target.model, params)
                try:
                    async with asyncio.timeout(timeout):
                        first = await stream.__anext__()
                except StopAsyncIteration:
                    health.in_flight -= 1
                    self._record_success(target.provider, self._clock() - started)
                    yield RouteChunk(target.provider, target.model, "", done=True, fallback=fallback,
                                     deployment=deployment.id)
                    return
                except (ProviderUnavailableError, TimeoutError) as exc:
                    health.in_flight -= 1
                    await self._close(stream)
                    error = exc if isinstance(exc, ProviderUnavailableError) else self._timed_out(target.provider, timeout)
                    self._record_failure(target.provider, error)
                    last_error = error
                    if not self._should_retry(error, attempt):
                        break
                    await self._backoff(target.provider, attempt, deadline)
                    continue
                except BaseException:
                    health.in_flight -= 1
                    health.record_neutral()
                    await self._close(stream)
                    raise

                # Committed to this provider from here on.
                ttft = self._clock() - started
                TTFT.labels(target.provider).observe(ttft)
                if fallback:
                    FALLBACKS.labels(target.provider).inc()
                # `async for` does not close `relay` if *we* are closed
                # (client disconnect), so close it explicitly - that's what
                # tears down the upstream request.
                relay = self._relay(deployment, stream, first, started, ttft, fallback, deadline)
                try:
                    async for chunk in relay:
                        yield chunk
                finally:
                    await self._close(relay)
                return

        raise self._exhausted(targets, last_error, 0)

    async def _relay(
        self,
        deployment: Deployment,
        stream: AsyncIterator,
        first,
        started: float,
        ttft: float,
        fallback: bool,
        deadline: Optional[float],
    ) -> AsyncIterator[RouteChunk]:
        target = deployment.target
        health = self.health[target.provider]
        idle_timeout = self._provider_timeouts.get(target.provider)
        last_usage = first.usage
        finish_reason = first.finish_reason
        outcome_recorded = False
        interrupted = False
        first_delta = first.delta[:self._max_stream_output_chars]
        output_chars = len(first_delta)
        if len(first.delta) > self._max_stream_output_chars:
            self._record_failure(target.provider, ProviderUnavailableError("stream output limit exceeded"))
            outcome_recorded = True
            interrupted = True
        try:
            yield RouteChunk(target.provider, target.model, first_delta, tool_calls=first.tool_calls)
            while not interrupted:
                try:
                    remaining = None if deadline is None else max(0.0, deadline - self._clock())
                    timeout = min(idle_timeout, remaining) if idle_timeout is not None and remaining is not None else (remaining if idle_timeout is None else idle_timeout)
                    async with asyncio.timeout(timeout):
                        event = await stream.__anext__()
                except StopAsyncIteration:
                    self._record_success(target.provider, self._clock() - started)
                    outcome_recorded = True
                    break
                except (ProviderUnavailableError, TimeoutError) as exc:
                    error = exc if isinstance(exc, ProviderUnavailableError) else self._timed_out(target.provider, idle_timeout)
                    self._record_failure(target.provider, error)
                    outcome_recorded = True
                    interrupted = True
                    logger.warning("stream from %s interrupted mid-response: %s", target.provider, error)
                    notice = f"\n\n_(connection to {target.provider} was interrupted - please try again)_"
                    yield RouteChunk(target.provider, target.model, notice)
                    break
                if event.usage:
                    last_usage = event.usage
                output_chars += len(event.delta)
                if output_chars > self._max_stream_output_chars:
                    self._record_failure(target.provider, ProviderUnavailableError("stream output limit exceeded"))
                    outcome_recorded = True
                    interrupted = True
                    logger.warning("stream from %s exceeded output limit", target.provider)
                    break
                finish_reason = event.finish_reason or finish_reason
                yield RouteChunk(target.provider, target.model, event.delta, tool_calls=event.tool_calls)
        finally:
            health.in_flight -= 1
            if not outcome_recorded:
                # client went away or something unexpected - neither a
                # success nor an availability failure, but a half-open
                # trial slot must not leak
                health.record_neutral()
            await self._close(stream)
        yield RouteChunk(
            target.provider, target.model, "", done=True, usage=last_usage, fallback=fallback, ttft=ttft,
            finish_reason="error" if interrupted else finish_reason, interrupted=interrupted,
            deployment=deployment.id,
        )

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()


# ---- construction from gateway config --------------------------------------


def provider_api_key(settings: Settings, setting: Optional[str]) -> str:
    """A provider's key: a Settings field (gemini_api_key, ...) or, for any
    other provider, the environment variable of that name (DEEPSEEK_API_KEY
    for "deepseek_api_key"), also read from .env."""
    if not setting:
        return ""
    value = getattr(settings, setting, None)
    if value is None:
        value = os.environ.get(setting.upper()) or (settings.model_extra or {}).get(setting.lower(), "")
    return value


def build_provider(
    name: str, config: ProviderConfig, settings: Settings, api_key: Optional[str] = None
) -> LLMProvider:
    """`api_key` is given for providers added from the admin panel (decrypted
    from the database); gateway.toml providers read it via api_key_setting."""
    if api_key is None:
        api_key = provider_api_key(settings, config.api_key_setting)
    if config.type == "gemini":
        provider: LLMProvider = GeminiProvider(
            api_key=api_key,
            base_url=config.base_url or GEMINI_API_BASE,
            default_model=config.default_model,
            timeout_seconds=config.timeout_seconds,
        )
        provider.name = name
        return provider
    if config.type == "openai_compatible":
        if not config.base_url:
            raise ValueError(f"provider {name!r}: openai_compatible requires base_url")
        return OpenAICompatibleProvider(
            name=name,
            base_url=config.base_url,
            api_key=api_key,
            default_model=config.default_model,
            timeout_seconds=config.timeout_seconds,
            require_api_key=bool(config.api_key_setting or api_key),
            auth_header=config.auth_header,
        )
    return MockProvider(
        name=name,
        default_model=config.default_model,
        latency_seconds=config.mock_latency_seconds,
        failure_rate=config.mock_failure_rate,
    )


def build_router(
    config: GatewayConfig, settings: Settings, provider_keys: Optional[Dict[str, str]] = None
) -> ModelRouter:
    keys = provider_keys or {}
    enabled = {name: cfg for name, cfg in config.providers.items() if cfg.enabled}
    providers = {name: build_provider(name, cfg, settings, keys.get(name)) for name, cfg in enabled.items()}
    capabilities = {
        name: frozenset(cfg.capabilities) if cfg.capabilities is not None else DEFAULT_CAPABILITIES[cfg.type]
        for name, cfg in enabled.items()
    }

    clashes = set(config.deployments) & (set(config.providers) | set(config.models))
    if clashes:
        raise ValueError(f"deployment ids must differ from provider and alias names: {sorted(clashes)}")
    deployments = {}
    for deployment_id, deployment in config.deployments.items():
        if deployment.provider not in config.providers:
            raise ValueError(f"deployment {deployment_id!r} names an unknown provider {deployment.provider!r}")
        if deployment.provider in enabled:
            caps = frozenset(deployment.capabilities) if deployment.capabilities is not None else None
            deployments[deployment_id] = (deployment.provider, deployment.model, caps)

    def usable(spec: str) -> bool:
        if spec in config.deployments:
            return spec in deployments
        if spec.partition("/")[0] not in config.providers:
            raise ValueError(f"model alias target {spec!r} is neither a deployment nor a configured provider")
        return spec.partition("/")[0] in enabled

    aliases: Dict[str, List[GroupSpec]] = {}
    for alias, alias_config in config.models.items():
        groups = [[spec for spec in group if usable(spec)] for group in alias_config.group_specs()]
        groups = [group for group in groups if group]
        if groups:
            aliases[alias] = groups
        else:
            logger.warning("model alias %r has no enabled providers - skipped", alias)

    return ModelRouter(
        providers,
        aliases,
        deployments=deployments,
        provider_capabilities=capabilities,
        retry=RetryPolicy(
            config.retry.max_retries,
            config.retry.backoff_base_seconds,
            config.retry.backoff_max_seconds,
        ),
        strategy=config.routing_strategy,
        pricing=config.pricing,
        request_timeout=config.request_timeout_seconds,
        max_stream_output_chars=config.max_stream_output_chars,
        provider_timeouts={name: cfg.timeout_seconds for name, cfg in enabled.items()},
        failure_threshold=config.circuit_breaker.failure_threshold,
        cooldown_seconds=config.circuit_breaker.cooldown_seconds,
    )


# The live router. Built from gateway.toml on first use, then replaced in
# place by app.gateway.runtime when providers, aliases or prices change in
# the admin panel - no restart. In-flight requests finish on the router they
# started with.
_router: Optional[ModelRouter] = None


def get_model_router() -> ModelRouter:
    global _router
    if _router is None:
        _router = build_router(get_gateway_config(), get_settings())
    return _router


def swap_router(new: ModelRouter) -> Optional[ModelRouter]:
    """Install `new`, carrying over circuit-breaker state for providers that
    still exist; returns the old router (the caller closes it later)."""
    global _router
    old = _router
    if old is not None:
        for name, health in old.health.items():
            if name in new.health:
                new.health[name] = health
    _router = new
    return old


def reset_router() -> Optional[ModelRouter]:
    """Forget the live router (shutdown, tests); returns it for closing."""
    global _router
    old, _router = _router, None
    return old
