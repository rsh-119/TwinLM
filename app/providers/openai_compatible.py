"""Adapter for any OpenAI-compatible chat completions API: Groq, OpenAI,
Ollama (/v1), NVIDIA NIM, vLLM, ... They differ only in base URL, key and
model names, which come from gateway.toml."""
import json
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from app.providers.base import (
    EmbeddingResult,
    GenerationParams,
    GenerationResult,
    PooledHTTPProvider,
    ProviderRateLimitedError,
    ProviderUnavailableError,
    StreamEvent,
    TokenUsage,
    raise_for_unavailable,
)

_NETWORK_ERRORS = (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)


def _is_token_rate_limited(status_code: int, body: bytes) -> bool:
    """On Groq's lower service tiers a single request can be rejected for
    exceeding the *account's* tokens-per-minute quota (observed: an 8000
    TPM limit on the free tier), and Groq reports that as a plain 413
    rather than the usual 429. Detected from the response body's error
    code rather than the status code alone, since 413 is otherwise a
    genuine "payload too large" that retrying/falling back wouldn't fix."""
    if status_code != 413:
        return False
    try:
        error = json.loads(body).get("error") or {}
    except (ValueError, AttributeError):
        return False
    return error.get("code") == "rate_limit_exceeded"


def _outgoing(message: Dict[str, Any]) -> Dict[str, Any]:
    """The message as OpenAI defines it. Drops `extra_content` (Gemini's
    thought signature, see gemini.py) from tool calls: strict OpenAI-style
    APIs reject unknown fields, which would break fallback from Gemini."""
    if not message.get("tool_calls"):
        return message
    calls = [{k: v for k, v in call.items() if k != "extra_content"} for call in message["tool_calls"]]
    return {**message, "tool_calls": calls}


class OpenAICompatibleProvider(PooledHTTPProvider):
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: Optional[str],
        default_model: str,
        timeout_seconds: float = 30.0,
        require_api_key: bool = True,
    ) -> None:
        super().__init__(timeout_seconds)
        self.name = name
        self.default_model = default_model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key or ""
        self._require_api_key = require_api_key

    def _headers(self) -> Dict[str, str]:
        if self._require_api_key and not self._api_key:
            raise ProviderUnavailableError(f"{self.name} api key is not configured")
        return {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

    def _check_status(self, status_code: int, body: bytes) -> None:
        if _is_token_rate_limited(status_code, body):
            raise ProviderRateLimitedError(
                f"{self.name} rate-limited this request: {body.decode('utf-8', 'replace')[:500]}"
            )
        raise_for_unavailable(self.name, status_code)

    def _json(self, raw: bytes) -> Dict:
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ProviderUnavailableError(f"{self.name} returned malformed JSON") from exc
        if not isinstance(data, dict):
            raise ProviderUnavailableError(f"{self.name} returned unexpected JSON")
        return data

    def _payload(self, messages: List[Dict[str, Any]], model: str, params: Optional[GenerationParams]) -> Dict:
        return {
            "model": model or self.default_model,
            "messages": [_outgoing(m) for m in messages],
            **(params or {}),
        }

    async def generate(
        self, messages: List[Dict[str, str]], model: str, params: Optional[GenerationParams] = None
    ) -> GenerationResult:
        headers = self._headers()
        try:
            response = await self.client.post(
                f"{self._base_url}/chat/completions",
                json=self._payload(messages, model, params),
                headers=headers,
            )
        except _NETWORK_ERRORS as exc:
            raise ProviderUnavailableError(f"{self.name} connection error: {exc}") from exc

        self._check_status(response.status_code, response.content)
        response.raise_for_status()

        data = self._json(response.content)
        content = self._extract_text(data)  # validates the shape
        choice = data["choices"][0]
        return GenerationResult(
            content=content,
            usage=self._extract_usage(data),
            tool_calls=choice["message"].get("tool_calls") or None,
            finish_reason=choice.get("finish_reason"),
        )

    async def generate_stream(
        self, messages: List[Dict[str, str]], model: str, params: Optional[GenerationParams] = None
    ) -> AsyncIterator[StreamEvent]:
        """SSE in OpenAI's delta shape. `stream_options.include_usage` makes
        the provider append one final event carrying the turn's usage."""
        headers = self._headers()
        payload = {
            **self._payload(messages, model, params),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        try:
            async with self.client.stream(
                "POST", f"{self._base_url}/chat/completions", json=payload, headers=headers
            ) as response:
                if response.status_code >= 400:
                    self._check_status(response.status_code, await response.aread())
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[len("data:"):].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    chunk = self._json(raw.encode())
                    text = self._extract_stream_text(chunk)
                    usage = self._extract_usage(chunk)
                    choice = (chunk.get("choices") or [{}])[0]
                    tool_calls = (choice.get("delta") or {}).get("tool_calls") or None
                    finish_reason = choice.get("finish_reason")
                    if text or usage or tool_calls or finish_reason:
                        yield StreamEvent(text, usage, tool_calls, finish_reason)
        except _NETWORK_ERRORS as exc:
            raise ProviderUnavailableError(f"{self.name} connection error: {exc}") from exc

    async def embed(self, texts: List[str], model: str) -> EmbeddingResult:
        headers = self._headers()
        try:
            response = await self.client.post(
                f"{self._base_url}/embeddings",
                json={"model": model or self.default_model, "input": texts},
                headers=headers,
            )
        except _NETWORK_ERRORS as exc:
            raise ProviderUnavailableError(f"{self.name} connection error: {exc}") from exc

        self._check_status(response.status_code, response.content)
        response.raise_for_status()

        data = self._json(response.content)
        try:
            items = sorted(data["data"], key=lambda item: item["index"])
            vectors = [item["embedding"] for item in items]
        except (KeyError, TypeError) as exc:
            raise ProviderUnavailableError(f"unexpected {self.name} embeddings shape: {exc}") from exc
        usage = data.get("usage") or {}
        return EmbeddingResult(
            vectors=vectors, usage=TokenUsage.reported(usage.get("prompt_tokens"), 0, usage.get("total_tokens"))
        )

    def _extract_text(self, data: Dict) -> str:
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderUnavailableError(f"unexpected {self.name} response shape: {exc}") from exc

    @staticmethod
    def _extract_usage(data: Dict) -> Optional[TokenUsage]:
        usage = data.get("usage")
        if not usage:
            return None
        # completion_tokens already includes reasoning tokens (OpenAI semantics)
        return TokenUsage.reported(usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens"))

    @staticmethod
    def _extract_stream_text(chunk: Dict) -> str:
        """Tolerant of the final `{"choices":[{"delta":{},"finish_reason":...}]}`
        event and the usage-only event, which carry no content."""
        choices = chunk.get("choices") or []
        if not choices:
            return ""
        return (choices[0].get("delta") or {}).get("content") or ""
