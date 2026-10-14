"""Common provider interface. Everything else in the gateway depends only on
this, never on a concrete provider, so a new one can be added without
touching the router or the API layer.
"""
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, NamedTuple, Optional

import httpx

# Failures that mean "try another provider" rather than "the request was bad".
# 400/401/403 are deliberately excluded - those should surface as-is.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Optional request parameters, keyed by their OpenAI names ("temperature",
# "top_p", "max_tokens", "stop", "tools", "tool_choice",
# "parallel_tool_calls"). Absent keys mean "provider default"; each adapter
# maps them to its own request shape.
GenerationParams = Dict[str, Any]


def text_of(content: Any) -> str:
    """The text of an OpenAI message `content`: a string, None (assistant
    turn that only calls tools), or a list of content parts."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if part.get("type") == "text")


class ProviderUnavailableError(Exception):
    """Rate limited, down, unreachable, or timed out. The router catches
    this - and only this - to decide whether to retry or fall back."""


class ProviderRateLimitedError(ProviderUnavailableError):
    """The provider throttled us (429, or Groq's 413 TPM limit). Retrying
    the same provider right away won't help, so the router skips straight
    to the next fallback."""


class ProviderClientError(Exception):
    """The upstream rejected the request shape or payload."""


class ProviderCredentialError(Exception):
    """The upstream rejected this gateway's credentials."""


def raise_for_unavailable(provider: str, status_code: int) -> None:
    """Shared status-code classification for HTTP adapters."""
    if status_code == 429:
        raise ProviderRateLimitedError(f"{provider} returned status 429")
    if status_code in RETRYABLE_STATUS_CODES:
        raise ProviderUnavailableError(f"{provider} returned status {status_code}")
    if status_code in (401, 403):
        raise ProviderCredentialError(f"{provider} rejected configured credentials (status {status_code})")
    if 400 <= status_code < 500:
        raise ProviderClientError(f"{provider} rejected the request (status {status_code})")


class TokenUsage(NamedTuple):
    """Token counts for one provider call, as reported by the provider's own
    response - not counted locally, so it always matches what the provider
    actually billed for."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    @classmethod
    def reported(cls, prompt: Optional[int], completion: Optional[int], total: Optional[int] = None) -> Optional["TokenUsage"]:
        """None unless the provider reported both input and output counts:
        a missing count is unknown, never zero."""
        if not isinstance(prompt, int) or not isinstance(completion, int):
            return None
        return cls(prompt, completion, total if isinstance(total, int) else prompt + completion)


class GenerationResult(NamedTuple):
    """What `generate` returns: the reply text, plus token usage when the
    provider's response reports it. `tool_calls` are in OpenAI's shape;
    `finish_reason` uses OpenAI's names (stop, length, tool_calls,
    content_filter), None when the provider didn't say."""

    content: str
    usage: Optional[TokenUsage] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    finish_reason: Optional[str] = None


class EmbeddingResult(NamedTuple):
    vectors: List[List[float]]
    usage: Optional[TokenUsage] = None


class StreamEvent(NamedTuple):
    """One item yielded by `generate_stream`: a piece of reply text, plus
    token usage *if* this particular event carries it - providers report
    usage once, on their last event, not on every chunk of text."""

    delta: str
    usage: Optional[TokenUsage] = None
    # OpenAI streamed tool-call deltas (each with an `index`)
    tool_calls: Optional[List[Dict[str, Any]]] = None
    finish_reason: Optional[str] = None


class LLMProvider(ABC):
    """Interface every provider adapter must implement."""

    name: str

    @abstractmethod
    async def generate(
        self, messages: List[Dict[str, str]], model: str, params: Optional[GenerationParams] = None
    ) -> GenerationResult:
        """Send `messages` ({"role", "content"} dicts) to the provider and
        return the assistant's reply text, plus token usage when the
        provider's response reports it.

        Raises ProviderUnavailableError on retryable failures; any other
        error (bad request, auth, ...) propagates unchanged.
        """
        raise NotImplementedError

    async def generate_stream(
        self, messages: List[Dict[str, str]], model: str, params: Optional[GenerationParams] = None
    ) -> AsyncIterator[StreamEvent]:
        """Like `generate`, but yields the reply incrementally as it's
        produced, so the caller can display it as it streams in.

        Same error contract as `generate`: raising before the first chunk
        is yielded is what lets the router fall back to another provider;
        raising after that isn't recoverable (some of the reply is already
        with the caller), and the router just ends the stream there.

        Providers with a real streaming endpoint (Gemini, Groq) override
        this. The default just wraps the one-shot `generate` call as a
        single event, so a provider that only ever implements `generate`
        (like a test fake) still works with the streaming code path.
        """
        result = await self.generate(messages, model, params)
        tool_calls = [{"index": i, **call} for i, call in enumerate(result.tool_calls or [])] or None
        yield StreamEvent(result.content, result.usage, tool_calls, result.finish_reason)

    async def embed(self, texts: List[str], model: str) -> EmbeddingResult:
        """Embedding vectors for `texts`, in order. Providers without an
        embeddings API leave this as-is; the router then skips them."""
        raise NotImplementedError(f"{self.name} does not support embeddings")

    async def aclose(self) -> None:
        """Release pooled connections. Called on app shutdown."""


class PooledHTTPProvider(LLMProvider):
    """Shared base for HTTP adapters: one pooled `httpx.AsyncClient` per
    provider instead of a new connection (and TLS handshake) per call."""

    def __init__(self, timeout_seconds: float) -> None:
        self._timeout = timeout_seconds
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        # Created lazily so it binds to the running event loop.
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
