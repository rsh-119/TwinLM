"""Groq provider adapter.

Groq exposes an OpenAI-compatible chat completions API, so this adapter is a
thin translation to/from that request/response shape - no framework needed.
"""
from typing import Dict, List, Optional

import httpx

from app.config import get_settings
from app.providers.base import RETRYABLE_STATUS_CODES, LLMProvider, ProviderUnavailableError

GROQ_API_BASE = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "llama-3.1-8b-instant"


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

    @staticmethod
    def _extract_text(data: Dict) -> str:
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise ProviderUnavailableError(f"unexpected groq response shape: {exc}") from exc
