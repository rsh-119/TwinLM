"""Groq provider adapter. Groq's API is OpenAI-compatible, so this is just a
thin translation to/from that request/response shape.
"""
import json
from typing import AsyncIterator, Dict, List, Optional

import httpx

from app.config import get_settings
from app.providers.base import RETRYABLE_STATUS_CODES, LLMProvider, ProviderUnavailableError

GROQ_API_BASE = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"


class GroqProvider(LLMProvider):
    """Adapter for Groq's OpenAI-compatible chat completions API."""

    name = "groq"

    def __init__(self, api_key: Optional[str] = None, base_url: str = GROQ_API_BASE) -> None:
        self._api_key = api_key if api_key is not None else get_settings().groq_api_key
        self._base_url = base_url.rstrip("/")

    async def generate(self, messages: List[Dict[str, str]], model: str) -> str:
        """Call Groq's chat completions endpoint and return the reply text."""
        if not self._api_key:
            raise ProviderUnavailableError("groq api key is not configured")

        model = model or DEFAULT_GROQ_MODEL
        payload = {
            "model": model,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = f"{self._base_url}/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            raise ProviderUnavailableError(f"groq connection error: {exc}") from exc

        if response.status_code in RETRYABLE_STATUS_CODES:
            raise ProviderUnavailableError(f"groq returned status {response.status_code}")
        response.raise_for_status()

        return self._extract_text(response.json())

    async def generate_stream(self, messages: List[Dict[str, str]], model: str) -> AsyncIterator[str]:
        """Call Groq's chat completions endpoint with `stream: true` (SSE,
        OpenAI's delta shape) and yield the reply text as it arrives."""
        if not self._api_key:
            raise ProviderUnavailableError("groq api key is not configured")

        model = model or DEFAULT_GROQ_MODEL
        payload = {
            "model": model,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = f"{self._base_url}/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as response:
                    if response.status_code in RETRYABLE_STATUS_CODES:
                        raise ProviderUnavailableError(f"groq returned status {response.status_code}")
                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if not data or data == "[DONE]":
                            continue
                        text = self._extract_stream_text(json.loads(data))
                        if text:
                            yield text
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            raise ProviderUnavailableError(f"groq connection error: {exc}") from exc

    @staticmethod
    def _extract_text(data: Dict) -> str:
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise ProviderUnavailableError(f"unexpected groq response shape: {exc}") from exc

    @staticmethod
    def _extract_stream_text(chunk: Dict) -> str:
        """Same shape as `_extract_text`, but for one SSE delta event -
        tolerant of the final `{"choices":[{"delta":{},"finish_reason":...}]}`
        event, which carries no content."""
        choices = chunk.get("choices") or []
        if not choices:
            return ""
        return choices[0].get("delta", {}).get("content") or ""
