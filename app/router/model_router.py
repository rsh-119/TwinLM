"""Picks a provider for a chat request and handles fallback for model="auto"."""
import logging
from functools import lru_cache
from typing import AsyncIterator, Dict, List, NamedTuple, Optional

from app.providers.base import LLMProvider, ProviderUnavailableError
from app.providers.gemini import DEFAULT_GEMINI_MODEL, GeminiProvider
from app.providers.groq import DEFAULT_GROQ_MODEL, GroqProvider

logger = logging.getLogger("twinlm.router")


class NoProviderAvailableError(Exception):
    """Every provider in the fallback chain was unavailable."""


class RouteResult(NamedTuple):
    provider: str
    model: str
    content: str


class RouteChunk(NamedTuple):
    """One piece of a streamed reply. `provider`/`model` are set from the
    first chunk onward, so a caller can show "gemini is answering" before
    the reply is even finished. `done=True` marks the last chunk for a
    turn - `delta` may or may not be empty on that one."""

    provider: str
    model: str
    delta: str
    done: bool = False


class ModelRouter:
    """Routes a chat request to a provider, with fallback for model="auto"."""

    # `model` here is really a provider selector ("gemini", "groq", "auto"),
    # not a raw model id, so each provider just has one default.
    DEFAULT_MODELS = {
        "gemini": DEFAULT_GEMINI_MODEL,
        "groq": DEFAULT_GROQ_MODEL,
    }

    # Order to try for model="auto": primary, then fallbacks.
    AUTO_FALLBACK_ORDER = ["gemini", "groq"]

    def __init__(self, providers: Dict[str, LLMProvider]) -> None:
        self._providers = providers

    async def route(self, model: str, messages: List[Dict[str, str]]) -> RouteResult:
        """An explicit provider name calls that provider directly, no
        fallback. "auto" tries AUTO_FALLBACK_ORDER in turn, falling back
        only on ProviderUnavailableError.
        """
        if model == "auto":
            return await self._route_auto(messages)
        return await self._route_explicit(model, messages)

    async def _route_explicit(self, model: str, messages: List[Dict[str, str]]) -> RouteResult:
        provider = self._providers.get(model)
        if provider is None:
            raise ValueError(f"unknown model or provider: {model!r}")
        model_name = self.DEFAULT_MODELS.get(model, model)
        content = await provider.generate(messages, model_name)
        return RouteResult(provider=model, model=model_name, content=content)

    async def _route_auto(self, messages: List[Dict[str, str]]) -> RouteResult:
        last_error: Optional[Exception] = None
        for provider_name in self.AUTO_FALLBACK_ORDER:
            provider = self._providers.get(provider_name)
            if provider is None:
                continue
            model_name = self.DEFAULT_MODELS.get(provider_name, provider_name)
            try:
                content = await provider.generate(messages, model_name)
                return RouteResult(provider=provider_name, model=model_name, content=content)
            except ProviderUnavailableError as exc:
                logger.warning("provider unavailable, falling back: provider=%s error=%s", provider_name, exc)
                last_error = exc
                continue
        raise NoProviderAvailableError("all providers unavailable") from last_error

    async def route_stream(self, model: str, messages: List[Dict[str, str]]) -> AsyncIterator[RouteChunk]:
        """Streaming counterpart to `route`. Same fallback rule as `_route_auto`,
        with one addition it doesn't need to worry about: fallback can only
        happen *before* the first chunk of a provider's reply goes out, since
        once a chunk is yielded here it's on its way to an HTTP client and
        can't be recalled. A provider that fails after that point ends the
        stream (with a short note appended) instead of switching providers.
        """
        if model == "auto":
            async for chunk in self._route_stream_auto(messages):
                yield chunk
            return
        async for chunk in self._route_stream_explicit(model, messages):
            yield chunk

    async def _route_stream_explicit(
        self, model: str, messages: List[Dict[str, str]]
    ) -> AsyncIterator[RouteChunk]:
        provider = self._providers.get(model)
        if provider is None:
            raise ValueError(f"unknown model or provider: {model!r}")
        model_name = self.DEFAULT_MODELS.get(model, model)
        async for text in provider.generate_stream(messages, model_name):
            yield RouteChunk(provider=model, model=model_name, delta=text, done=False)
        yield RouteChunk(provider=model, model=model_name, delta="", done=True)

    async def _route_stream_auto(self, messages: List[Dict[str, str]]) -> AsyncIterator[RouteChunk]:
        last_error: Optional[Exception] = None
        for provider_name in self.AUTO_FALLBACK_ORDER:
            provider = self._providers.get(provider_name)
            if provider is None:
                continue
            model_name = self.DEFAULT_MODELS.get(provider_name, provider_name)
            stream = provider.generate_stream(messages, model_name)

            try:
                first = await stream.__anext__()
            except StopAsyncIteration:
                yield RouteChunk(provider_name, model_name, "", done=True)
                return
            except ProviderUnavailableError as exc:
                logger.warning("provider unavailable, falling back: provider=%s error=%s", provider_name, exc)
                last_error = exc
                continue

            # A chunk made it out of the provider - from here on we're
            # committed to it even if it fails partway through.
            yield RouteChunk(provider_name, model_name, first, done=False)
            try:
                async for text in stream:
                    yield RouteChunk(provider_name, model_name, text, done=False)
            except ProviderUnavailableError as exc:
                logger.warning("stream from %s interrupted mid-response: %s", provider_name, exc)
                notice = f"\n\n_(connection to {provider_name} was interrupted - please try again)_"
                yield RouteChunk(provider_name, model_name, notice, done=False)
            yield RouteChunk(provider_name, model_name, "", done=True)
            return
        raise NoProviderAvailableError("all providers unavailable") from last_error


def build_default_providers() -> Dict[str, LLMProvider]:
    return {
        "gemini": GeminiProvider(),
        "groq": GroqProvider(),
    }


@lru_cache
def get_model_router() -> ModelRouter:
    return ModelRouter(build_default_providers())
