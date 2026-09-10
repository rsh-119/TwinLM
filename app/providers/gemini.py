"""Gemini provider adapter - converts to/from Gemini's generateContent shape."""
import json
from typing import AsyncIterator, Dict, List, Optional

import httpx

from app.config import get_settings
from app.providers.base import RETRYABLE_STATUS_CODES, LLMProvider, ProviderUnavailableError

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"

# Gemini calls the assistant role "model" instead of "assistant".
_ROLE_MAP = {"user": "user", "assistant": "model"}


class GeminiProvider(LLMProvider):
    """Adapter for Google's Gemini API."""

    name = "gemini"

    def __init__(self, api_key: Optional[str] = None, base_url: str = GEMINI_API_BASE) -> None:
        self._api_key = api_key if api_key is not None else get_settings().gemini_api_key
        self._base_url = base_url.rstrip("/")

    async def generate(self, messages: List[Dict[str, str]], model: str) -> str:
        """Call Gemini's generateContent endpoint and return the reply text."""
        if not self._api_key:
            raise ProviderUnavailableError("gemini api key is not configured")

        model = model or DEFAULT_GEMINI_MODEL
        system_instruction, contents = self._to_gemini_messages(messages)

        payload: Dict[str, object] = {"contents": contents}
        if system_instruction:
            payload["system_instruction"] = {"parts": [{"text": system_instruction}]}

        url = f"{self._base_url}/models/{model}:generateContent"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, params={"key": self._api_key}, json=payload)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            raise ProviderUnavailableError(f"gemini connection error: {exc}") from exc

        if response.status_code in RETRYABLE_STATUS_CODES:
            raise ProviderUnavailableError(f"gemini returned status {response.status_code}")
        response.raise_for_status()

        return self._extract_text(response.json())

    async def generate_stream(self, messages: List[Dict[str, str]], model: str) -> AsyncIterator[str]:
        """Call Gemini's streamGenerateContent endpoint (SSE) and yield the
        reply text as it arrives."""
        if not self._api_key:
            raise ProviderUnavailableError("gemini api key is not configured")

        model = model or DEFAULT_GEMINI_MODEL
        system_instruction, contents = self._to_gemini_messages(messages)

        payload: Dict[str, object] = {"contents": contents}
        if system_instruction:
            payload["system_instruction"] = {"parts": [{"text": system_instruction}]}

        url = f"{self._base_url}/models/{model}:streamGenerateContent"
        params = {"key": self._api_key, "alt": "sse"}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream("POST", url, params=params, json=payload) as response:
                    if response.status_code in RETRYABLE_STATUS_CODES:
                        raise ProviderUnavailableError(f"gemini returned status {response.status_code}")
                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if not data:
                            continue
                        for text in self._extract_stream_text(json.loads(data)):
                            yield text
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            raise ProviderUnavailableError(f"gemini connection error: {exc}") from exc

    @staticmethod
    def _to_gemini_messages(messages: List[Dict[str, str]]):
        """Split out system messages and map the rest to Gemini's `contents` shape."""
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        system_instruction = "\n\n".join(system_parts)

        contents = [
            {"role": _ROLE_MAP.get(m["role"], "user"), "parts": [{"text": m["content"]}]}
            for m in messages
            if m["role"] != "system"
        ]
        return system_instruction, contents

    @staticmethod
    def _extract_text(data: Dict) -> str:
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError) as exc:
            raise ProviderUnavailableError(f"unexpected gemini response shape: {exc}") from exc

    @staticmethod
    def _extract_stream_text(chunk: Dict) -> List[str]:
        """Same shape as `_extract_text`, but tolerant of the odd stream
        event that carries no candidate (e.g. one that only sets
        `promptFeedback`) - skip it instead of failing the whole stream."""
        candidates = chunk.get("candidates") or []
        if not candidates:
            return []
        parts = (candidates[0].get("content") or {}).get("parts", [])
        return [part.get("text", "") for part in parts if part.get("text")]
