"""Common provider interface and shared provider-level errors.

Every concrete provider (Gemini, Groq, ...) implements `LLMProvider` and is
otherwise free to talk to its own API however it needs to. The rest of the
gateway (router, conversation manager, chat API) only ever depends on this
interface - never on a concrete provider class - so a new provider can be
added without touching any core gateway logic.
"""
from abc import ABC, abstractmethod
from typing import Dict, List

# HTTP statuses that mean "this provider is temporarily unavailable, try the
# next one" as opposed to "the request itself was bad". Normal client errors
# (400/401/403) are deliberately excluded - those should surface as-is rather
# than trigger a confusing fallback.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class ProviderUnavailableError(Exception):
    """Raised when a provider cannot currently serve a request.

    Covers rate limiting, server errors, timeouts, and connection failures.
    The model router catches this specific exception - and only this one -
    to decide whether to fall back to the next provider.
    """


class LLMProvider(ABC):
    """Interface every LLM provider adapter must implement."""

    name: str

    @abstractmethod
    async def generate(self, messages: List[Dict[str, str]], model: str) -> str:
        """Send `messages` to the provider and return the assistant's reply text.

        Args:
            messages: Ordered list of {"role": ..., "content": ...} dicts,
                where role is "system", "user", or "assistant". Providers
                translate this into their own request format internally.
            model: The provider-specific model name to use.

        Returns:
            The assistant's reply text.

        Raises:
            ProviderUnavailableError: on retryable availability failures.
            Exception: any other error (bad request, auth, ...) propagates
                unchanged so it is not mistaken for an availability problem.
        """
        raise NotImplementedError
