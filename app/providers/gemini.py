"""Gemini provider adapter - converts to/from Gemini's generateContent shape."""
import json
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from app.config import get_settings
from app.providers.base import (
    connection_error,
    EmbeddingResult,
    GenerationParams,
    GenerationResult,
    PooledHTTPProvider,
    ProviderCredentialError,
    ProviderUnavailableError,
    StreamEvent,
    TokenUsage,
    raise_for_unavailable,
    text_of,
)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
DEFAULT_GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"

_NETWORK_ERRORS = (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)

# Gemini calls the assistant role "model" instead of "assistant".
_ROLE_MAP = {"user": "user", "assistant": "model"}

# Gemini finishReason -> OpenAI finish_reason. Unlisted reasons map to "stop".
_FINISH_REASONS = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "BLOCKLIST": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
    "SPII": "content_filter",
}

_TOOL_CHOICE_MODES = {"none": "NONE", "auto": "AUTO", "required": "ANY"}

# Gemini 3 rejects a replayed function call without the thought signature
# it issued for it. Calls it didn't issue (another provider answered that
# turn, or the client dropped `extra_content`) get Google's documented
# bypass value instead.
_SKIP_SIGNATURE = "skip_thought_signature_validator"

# OpenAI-named sampling params -> Gemini `generationConfig` fields.
_PARAM_MAP = {
    "temperature": "temperature",
    "top_p": "topP",
    "max_tokens": "maxOutputTokens",
    "stop": "stopSequences",
    "presence_penalty": "presencePenalty",
    "frequency_penalty": "frequencyPenalty",
    "seed": "seed",
}


class GeminiProvider(PooledHTTPProvider):
    """Adapter for Google's Gemini API."""

    name = "gemini"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = GEMINI_API_BASE,
        default_model: str = DEFAULT_GEMINI_MODEL,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(timeout_seconds)
        self.default_model = default_model
        self._api_key = api_key if api_key is not None else get_settings().gemini_api_key
        self._base_url = base_url.rstrip("/")

    async def generate(
        self, messages: List[Dict[str, str]], model: str, params: Optional[GenerationParams] = None
    ) -> GenerationResult:
        """Call Gemini's generateContent endpoint and return the reply text."""
        url = f"{self._base_url}/models/{model or self.default_model}:generateContent"
        try:
            response = await self.client.post(
                url, headers=self._headers(), json=self._build_payload(messages, params)
            )
        except _NETWORK_ERRORS as exc:
            raise connection_error("gemini", exc) from exc

        await self._check_status(response)
        response.raise_for_status()

        data = _parse_json(response.content)
        content = self._extract_text(data)  # validates the shape
        candidate = data["candidates"][0]
        tool_calls = _tool_calls((candidate.get("content") or {}).get("parts", []))
        return GenerationResult(content, self._extract_usage(data), tool_calls, _finish_reason(candidate, tool_calls))

    async def _check_status(self, response: httpx.Response) -> None:
        """Status classification, plus Gemini's way of rejecting a key: 400
        with reason API_KEY_INVALID (not 401/403). Only that reason code is
        looked for; the upstream body never reaches the error message."""
        if response.status_code == 400 and b"API_KEY_INVALID" in await response.aread():
            raise ProviderCredentialError(f"{self.name} rejected the API key (API_KEY_INVALID)")
        raise_for_unavailable(self.name, response.status_code)

    async def generate_stream(
        self, messages: List[Dict[str, str]], model: str, params: Optional[GenerationParams] = None
    ) -> AsyncIterator[StreamEvent]:
        """Call Gemini's streamGenerateContent endpoint (SSE) and yield the
        reply text as it arrives."""
        url = f"{self._base_url}/models/{model or self.default_model}:streamGenerateContent"
        try:
            async with self.client.stream(
                "POST",
                url,
                params={"alt": "sse"},
                headers=self._headers(),
                json=self._build_payload(messages, params),
            ) as response:
                await self._check_status(response)
                response.raise_for_status()

                tool_index = 0
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[len("data:"):].strip()
                    if not raw:
                        continue
                    chunk = _parse_json(raw.encode())
                    pieces = self._extract_stream_text(chunk)
                    usage = self._extract_usage(chunk)
                    candidate = (chunk.get("candidates") or [{}])[0]
                    # Gemini sends each function call whole, in one chunk
                    calls = _tool_calls((candidate.get("content") or {}).get("parts", []))
                    if calls:
                        calls = [{"index": tool_index + i, **call} for i, call in enumerate(calls)]
                        tool_index += len(calls)
                    finish = _finish_reason(candidate, tool_index > 0)
                    for text in pieces[:-1]:
                        yield StreamEvent(delta=text)
                    last = pieces[-1] if pieces else ""
                    # a chunk may carry only usage/feedback, no text
                    if last or usage or calls or finish:
                        yield StreamEvent(last, usage, calls, finish)
        except _NETWORK_ERRORS as exc:
            raise connection_error("gemini", exc) from exc

    async def embed(self, texts: List[str], model: str) -> EmbeddingResult:
        """batchEmbedContents. Gemini doesn't report token usage for
        embeddings, so usage is None."""
        model = model or DEFAULT_GEMINI_EMBEDDING_MODEL
        payload = {
            "requests": [
                {"model": f"models/{model}", "content": {"parts": [{"text": text}]}} for text in texts
            ]
        }
        try:
            response = await self.client.post(
                f"{self._base_url}/models/{model}:batchEmbedContents",
                headers=self._headers(),
                json=payload,
            )
        except _NETWORK_ERRORS as exc:
            raise connection_error("gemini", exc) from exc

        await self._check_status(response)
        response.raise_for_status()

        data = _parse_json(response.content)
        try:
            return EmbeddingResult(vectors=[item["values"] for item in data["embeddings"]])
        except (KeyError, TypeError) as exc:
            raise ProviderUnavailableError(f"unexpected gemini embeddings shape: {exc}") from exc

    def _headers(self) -> Dict[str, str]:
        if not self._api_key:
            raise ProviderUnavailableError("gemini api key is not configured")
        # Header, not the `?key=` query param: request URLs end up in logs
        # (httpx logs every one at INFO, and HTTPStatusError embeds it).
        return {"x-goog-api-key": self._api_key}

    @classmethod
    def _build_payload(
        cls, messages: List[Dict[str, str]], params: Optional[GenerationParams]
    ) -> Dict[str, object]:
        system_instruction, contents = cls._to_gemini_messages(messages)
        payload: Dict[str, object] = {"contents": contents}
        if system_instruction:
            payload["system_instruction"] = {"parts": [{"text": system_instruction}]}

        config = {_PARAM_MAP[k]: v for k, v in (params or {}).items() if k in _PARAM_MAP}
        if isinstance(config.get("stopSequences"), str):
            config["stopSequences"] = [config["stopSequences"]]
        if config:
            payload["generationConfig"] = config

        params = params or {}
        if params.get("tools"):
            payload["tools"] = [{"functionDeclarations": [_function_declaration(t) for t in params["tools"]]}]
        if params.get("tools") and params.get("tool_choice") is not None:
            payload["toolConfig"] = _tool_config(params["tool_choice"])
        return payload

    @staticmethod
    def _to_gemini_messages(messages: List[Dict[str, Any]]):
        """Split out system messages and map the rest to Gemini's `contents`
        shape: tool calls become functionCall parts, tool results
        functionResponse parts (consecutive results share one turn)."""
        system_parts = [text_of(m.get("content")) for m in messages if m["role"] == "system"]
        system_instruction = "\n\n".join(system_parts)

        call_names = {
            call.get("id"): call["function"]["name"] for m in messages for call in m.get("tool_calls") or []
        }
        contents: List[Dict[str, Any]] = []
        for m in messages:
            if m["role"] == "system":
                continue
            if m["role"] == "tool":
                name = m.get("name") or call_names.get(m["tool_call_id"])
                if not name:
                    raise ValueError(f"tool message {m['tool_call_id']!r} matches no earlier tool call")
                part = {"functionResponse": {"name": name, "response": _tool_response(m.get("content"))}}
                previous = contents[-1]["parts"] if contents and contents[-1]["role"] == "user" else []
                if previous and all("functionResponse" in p for p in previous):
                    previous.append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
                continue
            parts = _content_parts(m.get("content"))
            if m.get("tool_calls"):
                parts += _function_call_parts(m["tool_calls"])
            contents.append({"role": _ROLE_MAP.get(m["role"], "user"), "parts": parts})
        return system_instruction, contents

    @staticmethod
    def _extract_text(data: Dict) -> str:
        try:
            candidate = data["candidates"][0]
            if "content" not in candidate and candidate.get("finishReason"):
                return ""  # e.g. blocked by SAFETY: no content, finish_reason says why
            parts = candidate["content"].get("parts", [])  # absent when e.g. thinking used up max_tokens
            return "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderUnavailableError(f"unexpected gemini response shape: {exc}") from exc

    @staticmethod
    def _extract_usage(data: Dict) -> Optional[TokenUsage]:
        """`usageMetadata` is present on (non-streamed) responses and on
        streamed chunks alike, with cumulative counts - the caller only
        needs to remember the latest one it saw."""
        usage = data.get("usageMetadata")
        if not usage or not isinstance(usage.get("promptTokenCount"), int):
            return None
        # Thinking tokens are billed at the output rate; tool-use prompt
        # tokens at the input rate. An absent count here means none were used.
        prompt = usage["promptTokenCount"] + usage.get("toolUsePromptTokenCount", 0)
        completion = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
        return TokenUsage.reported(prompt, completion, usage.get("totalTokenCount"))

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


def _function_declaration(tool: Dict[str, Any]) -> Dict[str, Any]:
    if tool.get("type") != "function":
        raise ValueError(f"gemini supports only function tools, got {tool.get('type')!r}")
    fn = tool["function"]
    declaration = {"name": fn["name"]}
    if fn.get("description"):
        declaration["description"] = fn["description"]
    if fn.get("parameters"):
        # full JSON Schema, unlike `parameters` (an OpenAPI subset)
        declaration["parametersJsonSchema"] = fn["parameters"]
    return declaration


def _tool_config(choice: Any) -> Dict[str, Any]:
    if isinstance(choice, dict):
        name = choice["function"]["name"]
        return {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [name]}}
    if choice not in _TOOL_CHOICE_MODES:
        raise ValueError(f"unsupported tool_choice: {choice!r}")
    return {"functionCallingConfig": {"mode": _TOOL_CHOICE_MODES[choice]}}


def _content_parts(content: Any) -> List[Dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"text": content}]
    return [{"text": p["text"]} if p["type"] == "text" else _inline_image(p["image_url"]["url"]) for p in content]


def _inline_image(url: str) -> Dict[str, Any]:
    header, comma, data = url.partition(",")
    if not (header.startswith("data:") and header.endswith(";base64") and comma):
        raise ValueError("gemini accepts images only as base64 data: URLs")
    return {"inlineData": {"mimeType": header[len("data:"):-len(";base64")], "data": data}}


def _function_call_parts(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    parts = []
    for call in tool_calls:
        fn = call["function"]
        part: Dict[str, Any] = {"functionCall": {"name": fn["name"], "args": json.loads(fn.get("arguments") or "{}")}}
        signature = ((call.get("extra_content") or {}).get("google") or {}).get("thought_signature")
        if signature:
            part["thoughtSignature"] = signature
        parts.append(part)
    if parts and not any("thoughtSignature" in p for p in parts):
        parts[0]["thoughtSignature"] = _SKIP_SIGNATURE
    return parts


def _tool_response(content: Any) -> Dict[str, Any]:
    """functionResponse.response must be an object."""
    text = text_of(content)
    try:
        value = json.loads(text)
    except ValueError:
        value = text
    return value if isinstance(value, dict) else {"result": value}


def _tool_calls(parts: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """Gemini functionCall parts -> OpenAI tool_calls. The thought
    signature rides along in `extra_content`, as in Google's own
    OpenAI-compatible API, so it survives the client's round trip."""
    calls = []
    for part in parts:
        fc = part.get("functionCall")
        if not fc:
            continue
        call: Dict[str, Any] = {
            "id": fc.get("id") or f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {"name": fc.get("name", ""), "arguments": json.dumps(fc.get("args") or {})},
        }
        if part.get("thoughtSignature"):
            call["extra_content"] = {"google": {"thought_signature": part["thoughtSignature"]}}
        calls.append(call)
    return calls or None


def _finish_reason(candidate: Dict[str, Any], called_tools: Any) -> Optional[str]:
    reason = candidate.get("finishReason")
    if not reason:
        return None
    return "tool_calls" if called_tools else _FINISH_REASONS.get(reason, "stop")


def _parse_json(raw: bytes) -> Dict:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ProviderUnavailableError("gemini returned malformed JSON") from exc
    if not isinstance(data, dict):
        raise ProviderUnavailableError("gemini returned unexpected JSON")
    return data
