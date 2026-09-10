"""Common provider interface. Everything else in the gateway depends only on
this, never on a concrete provider, so a new one can be added without
touching the router or the API layer.
"""
from abc import ABC, abstractmethod
from typing import AsyncIterator, Dict, List

# Failures that mean "try another provider" rather than "the request was bad".
# 400/401/403 are deliberately excluded - those should surface as-is.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class ProviderUnavailableError(Exception):
    """Rate limited, down, unreachable, or timed out. The router catches
    this - and only this - to decide whether to fall back."""


class LLMProvider(ABC):
    """Interface every provider adapter must implement."""

    name: str

    @abstractmethod
    async def generate(self, messages: List[Dict[str, str]], model: str) -> str:
        """Send `messages` ({"role", "content"} dicts) to the provider and
        return the assistant's reply text.

        Raises ProviderUnavailableError on retryable failures; any other
        error (bad request, auth, ...) propagates unchanged.
        """
        raise NotImplementedError

    async def generate_stream(self, messages: List[Dict[str, str]], model: str) -> AsyncIterator[str]:
        """Like `generate`, but yields the reply incrementally as it's
        produced, so the caller can display it as it streams in.

        Same error contract as `generate`: raising before the first chunk
        is yielded is what lets the router fall back to another provider;
        raising after that isn't recoverable (some of the reply is already
        with the caller), and the router just ends the stream there.

        Providers with a real streaming endpoint (Gemini, Groq) override
        this. The default just wraps the one-shot `generate` call as a
        single chunk, so a provider that only ever implements `generate`
        (like a test fake) still works with the streaming code path.
        """
        yield await self.generate(messages, model)
