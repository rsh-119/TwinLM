"""Model router: picks a provider for a chat request and handles fallback.

The router is the only place that knows the mapping from a requested
`model` string to a concrete provider, and the only place that knows the
fallback order for `model="auto"`. It depends only on the `LLMProvider`
interface, so adding a new provider means adding one entry here - not
touching any other core logic.
"""
import logging
from functools import lru_cache
from typing import Dict, List, NamedTuple, Optional

from app.providers.base import LLMProvider, ProviderUnavailableError
from app.providers.gemini import DEFAULT_GEMINI_MODEL, GeminiProvider
from app.providers.groq import DEFAULT_GROQ_MODEL, GroqProvider

logger = logging.getLogger("llm_gateway.router")


class NoProviderAvailableError(Exception):
    """Raised when every provider in the fallback chain was unavailable."""


class RouteResult(NamedTuple):
    """The outcome of routing a request: who answered, and what they said."""

    provider: str
    model: str
    content: str


class ModelRouter:
    """Routes a chat request to a provider, with fallback for `model="auto"`."""

    # The concrete model id used when a request doesn't ask for a specific
    # one - here `model` is really a provider selector ("gemini", "groq",
    # "auto"), not a raw model id, so each provider has one default.
    DEFAULT_MODELS = {
        "gemini": DEFAULT_GEMINI_MODEL,
        "groq": DEFAULT_GROQ_MODEL,
    }

    # Order to try providers in for model="auto": primary, then fallbacks.
    AUTO_FALLBACK_ORDER = ["gemini", "groq"]

    def __init__(self, providers: Dict[str, LLMProvider]) -> None:
        """`providers` maps a provider name (e.g. "gemini") to an instance."""
        self._providers = providers

    async def route(self, model: str, messages: List[Dict[str, str]]) -> RouteResult:
        """Send `messages` to the right provider(s) and return the result.

        - An explicit provider name calls that provider directly; if it
          fails, the failure propagates unchanged (no fallback).
        - "auto" tries each provider in `AUTO_FALLBACK_ORDER` in turn,
          falling back only on `ProviderUnavailableError`.

        Raises:
            ValueError: `model` isn't "auto" and isn't a configured provider.
            NoProviderAvailableError: every provider in the "auto" chain failed.
            ProviderUnavailableError: the explicitly requested provider failed.
            Exception: any other error from the provider propagates as-is.
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


def build_default_providers() -> Dict[str, LLMProvider]:
    """Build the default provider instances, reading credentials from settings."""
    return {
        "gemini": GeminiProvider(),
        "groq": GroqProvider(),
    }


@lru_cache
def get_model_router() -> ModelRouter:
    """Return the process-wide `ModelRouter`, wired up with default providers."""
    return ModelRouter(build_default_providers())
