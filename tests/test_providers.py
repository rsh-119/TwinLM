"""Tests for provider-level helpers that don't need a real network call.

`GroqProvider.generate`/`generate_stream` themselves aren't unit-tested here
(they're thin httpx wrappers, already covered end-to-end by hitting the real
API during development) - this is specifically for `_is_token_rate_limited`,
the bit of response-shape detection that was actually wrong.
"""
import json

import httpx
import pytest

from app.providers.gemini import GeminiProvider
from app.providers.groq import GroqProvider, _is_token_rate_limited

# Groq's real response body when a request exceeds the account's
# tokens-per-minute quota - captured verbatim from a live 413 during
# debugging, not a guessed shape.
_REAL_TPM_LIMIT_BODY = (
    b'{"error":{"message":"Request too large for model `openai/gpt-oss-20b` '
    b'in organization `org_x` service tier `on_demand` on tokens per minute '
    b'(TPM): Limit 8000, Requested 8633, please reduce your message size and '
    b'try again.","type":"tokens","code":"rate_limit_exceeded"}}'
)


def test_recognizes_the_real_groq_tpm_limit_response():
    assert _is_token_rate_limited(413, _REAL_TPM_LIMIT_BODY) is True


def test_ignores_413_with_a_different_error_code():
    """A 413 that really does just mean "payload too large" (a different
    error code, or no recognizable body) shouldn't be treated as a
    retryable rate limit - retrying/falling back wouldn't fix that."""
    body = b'{"error":{"message":"body too large","code":"request_too_large"}}'
    assert _is_token_rate_limited(413, body) is False


def test_ignores_non_413_status_codes():
    assert _is_token_rate_limited(429, _REAL_TPM_LIMIT_BODY) is False
    assert _is_token_rate_limited(400, _REAL_TPM_LIMIT_BODY) is False
    assert _is_token_rate_limited(200, _REAL_TPM_LIMIT_BODY) is False


def test_tolerates_a_body_that_is_not_json():
    assert _is_token_rate_limited(413, b"not json at all") is False
    assert _is_token_rate_limited(413, b"") is False


# --- Request shape, via httpx.MockTransport (no network) ---

_GEMINI_REPLY = {
    "candidates": [{"content": {"parts": [{"text": "hi"}]}}],
    "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1, "totalTokenCount": 4},
}


@pytest.fixture
def mock_http(monkeypatch):
    """Routes every httpx.AsyncClient through a MockTransport. Returns
    (recorded requests, {url path suffix: response})."""
    requests, responses = [], {}
    real_client = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        for suffix, response in responses.items():
            if request.url.path.endswith(suffix):
                return response
        return httpx.Response(404)

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    return requests, responses


async def test_gemini_sends_key_in_header_never_in_url(mock_http):
    requests, responses = mock_http
    responses[":generateContent"] = httpx.Response(200, json=_GEMINI_REPLY)
    responses[":streamGenerateContent"] = httpx.Response(200, text=f"data: {json.dumps(_GEMINI_REPLY)}\n\n")
    provider = GeminiProvider(api_key="secret-key")

    result = await provider.generate([{"role": "user", "content": "q"}], "m")
    events = [e async for e in provider.generate_stream([{"role": "user", "content": "q"}], "m")]

    assert result.content == "hi"
    assert events[0].delta == "hi"
    assert len(requests) == 2
    for request in requests:
        assert "secret-key" not in str(request.url)
        assert request.headers["x-goog-api-key"] == "secret-key"


@pytest.mark.parametrize("status, error", [(400, "ProviderClientError"), (401, "ProviderCredentialError"), (403, "ProviderCredentialError"), (429, "ProviderRateLimitedError"), (503, "ProviderUnavailableError")])
async def test_gemini_classifies_upstream_status_without_leaking_body(mock_http, status, error):
    _, responses = mock_http
    responses[":generateContent"] = httpx.Response(status, text="secret-upstream-detail")
    provider = GeminiProvider(api_key="k")
    with pytest.raises(Exception) as caught:
        await provider.generate([{"role": "user", "content": "q"}], "m")
    assert type(caught.value).__name__ == error
    assert "secret-upstream-detail" not in str(caught.value)


async def test_gemini_maps_sampling_params_to_generation_config(mock_http):
    requests, responses = mock_http
    responses[":generateContent"] = httpx.Response(200, json=_GEMINI_REPLY)

    await GeminiProvider(api_key="k").generate(
        [{"role": "user", "content": "q"}],
        "m",
        {"temperature": 0.1, "top_p": 0.9, "max_tokens": 20, "stop": "END"},
    )

    assert json.loads(requests[0].content)["generationConfig"] == {
        "temperature": 0.1,
        "topP": 0.9,
        "maxOutputTokens": 20,
        "stopSequences": ["END"],
    }


async def test_gemini_omits_generation_config_without_params(mock_http):
    requests, responses = mock_http
    responses[":generateContent"] = httpx.Response(200, json=_GEMINI_REPLY)

    await GeminiProvider(api_key="k").generate([{"role": "user", "content": "q"}], "m")

    assert "generationConfig" not in json.loads(requests[0].content)


async def test_groq_passes_sampling_params_through_but_keeps_stream_flags(mock_http):
    requests, responses = mock_http
    responses["/chat/completions"] = httpx.Response(200, text="data: [DONE]\n\n")

    events = GroqProvider(api_key="k").generate_stream(
        [{"role": "user", "content": "q"}], "m", {"temperature": 0.3, "max_tokens": 5, "stream": False}
    )
    _ = [e async for e in events]

    payload = json.loads(requests[0].content)
    assert payload["temperature"] == 0.3
    assert payload["max_tokens"] == 5
    assert payload["stream"] is True


# --- Tools, content parts and finish reasons ---

_WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Weather for a city",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "additionalProperties": False},
    },
}


def _assistant_calls(*calls):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}, **extra}
            for call_id, name, args, extra in calls
        ],
    }


def test_gemini_maps_tools_tool_choice_images_and_tool_history():
    signed = {"extra_content": {"google": {"thought_signature": "sig-1"}}}
    messages = [
        {"role": "system", "content": [{"type": "text", "text": "Be brief."}]},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Weather here?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        },
        _assistant_calls(("c1", "get_weather", '{"city": "Pune"}', signed), ("c2", "get_weather", '{"city": "Goa"}', {})),
        {"role": "tool", "tool_call_id": "c1", "content": '{"temp": 31}'},
        {"role": "tool", "tool_call_id": "c2", "content": "sunny"},
    ]
    payload = GeminiProvider._build_payload(
        messages, {"tools": [_WEATHER_TOOL], "tool_choice": {"type": "function", "function": {"name": "get_weather"}}}
    )

    assert payload["system_instruction"] == {"parts": [{"text": "Be brief."}]}
    assert payload["tools"] == [{"functionDeclarations": [{
        "name": "get_weather",
        "description": "Weather for a city",
        "parametersJsonSchema": _WEATHER_TOOL["function"]["parameters"],
    }]}]
    assert payload["toolConfig"] == {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["get_weather"]}}
    user, model, results = payload["contents"]
    assert user["parts"] == [{"text": "Weather here?"}, {"inlineData": {"mimeType": "image/png", "data": "AAAA"}}]
    assert model == {"role": "model", "parts": [
        {"functionCall": {"name": "get_weather", "args": {"city": "Pune"}}, "thoughtSignature": "sig-1"},
        {"functionCall": {"name": "get_weather", "args": {"city": "Goa"}}},
    ]}
    # parallel results share one turn; non-object results are wrapped
    assert results == {"role": "user", "parts": [
        {"functionResponse": {"name": "get_weather", "response": {"temp": 31}}},
        {"functionResponse": {"name": "get_weather", "response": {"result": "sunny"}}},
    ]}


def test_gemini_replayed_calls_without_signature_get_the_bypass_value():
    payload = GeminiProvider._build_payload(
        [{"role": "user", "content": "q"}, _assistant_calls(("c1", "f", "{}", {}), ("c2", "f", "{}", {}))], None
    )
    parts = payload["contents"][1]["parts"]
    assert parts[0]["thoughtSignature"] == "skip_thought_signature_validator"
    assert "thoughtSignature" not in parts[1]


@pytest.mark.parametrize("choice, config", [
    ("none", {"mode": "NONE"}), ("auto", {"mode": "AUTO"}), ("required", {"mode": "ANY"}),
])
def test_gemini_maps_string_tool_choice(choice, config):
    payload = GeminiProvider._build_payload(
        [{"role": "user", "content": "q"}], {"tools": [_WEATHER_TOOL], "tool_choice": choice}
    )
    assert payload["toolConfig"] == {"functionCallingConfig": config}


def test_gemini_drops_tool_choice_without_tools():
    # Gemini rejects toolConfig with no functionDeclarations
    payload = GeminiProvider._build_payload([{"role": "user", "content": "q"}], {"tool_choice": "auto"})
    assert "toolConfig" not in payload


@pytest.mark.parametrize("messages, params, error", [
    ([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://x/cat.png"}}]}], None, "data: URLs"),
    ([{"role": "user", "content": "q"}, {"role": "tool", "tool_call_id": "nope", "content": "x"}], None, "no earlier tool call"),
    ([{"role": "user", "content": "q"}], {"tools": [{"type": "code_interpreter"}]}, "only function tools"),
    ([{"role": "user", "content": "q"}], {"tools": [_WEATHER_TOOL], "tool_choice": "sometimes"}, "unsupported tool_choice"),
])
def test_gemini_rejects_what_it_cannot_express(messages, params, error):
    with pytest.raises(ValueError, match=error):
        GeminiProvider._build_payload(messages, params)


async def test_gemini_function_calls_become_openai_tool_calls(mock_http):
    _, responses = mock_http
    responses[":generateContent"] = httpx.Response(200, json={"candidates": [{
        "content": {"parts": [{"functionCall": {"name": "get_weather", "args": {"city": "Pune"}}, "thoughtSignature": "sig"}]},
        "finishReason": "STOP",
    }]})

    result = await GeminiProvider(api_key="k").generate([{"role": "user", "content": "q"}], "m", {"tools": [_WEATHER_TOOL]})

    (call,) = result.tool_calls
    assert call["id"].startswith("call_")
    assert call["type"] == "function"
    assert call["function"] == {"name": "get_weather", "arguments": '{"city": "Pune"}'}
    assert call["extra_content"] == {"google": {"thought_signature": "sig"}}
    assert result.finish_reason == "tool_calls"


@pytest.mark.parametrize("candidate, content, finish_reason", [
    ({"content": {"parts": [{"text": "cut"}]}, "finishReason": "MAX_TOKENS"}, "cut", "length"),
    ({"finishReason": "SAFETY"}, "", "content_filter"),
    ({"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}, "ok", "stop"),
    ({"content": {"parts": [{"text": "ok"}]}}, "ok", None),
])
async def test_gemini_finish_reasons(mock_http, candidate, content, finish_reason):
    _, responses = mock_http
    responses[":generateContent"] = httpx.Response(200, json={"candidates": [candidate]})

    result = await GeminiProvider(api_key="k").generate([{"role": "user", "content": "q"}], "m")

    assert (result.content, result.finish_reason) == (content, finish_reason)


async def test_gemini_stream_indexes_tool_calls_and_reports_finish(mock_http):
    _, responses = mock_http
    chunks = [
        {"candidates": [{"content": {"parts": [{"functionCall": {"name": "a", "args": {}}}]}}]},
        {"candidates": [{"content": {"parts": [{"functionCall": {"name": "b", "args": {"x": 1}}}]}, "finishReason": "STOP"}]},
    ]
    responses[":streamGenerateContent"] = httpx.Response(200, text="".join(f"data: {json.dumps(c)}\n\n" for c in chunks))

    events = [e async for e in GeminiProvider(api_key="k").generate_stream([{"role": "user", "content": "q"}], "m")]

    calls = [call for e in events for call in e.tool_calls or []]
    assert [(c["index"], c["function"]["name"]) for c in calls] == [(0, "a"), (1, "b")]
    assert events[-1].finish_reason == "tool_calls"


async def test_openai_compatible_forwards_tools_and_returns_tool_calls(mock_http):
    requests, responses = mock_http
    call = {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": "{}"}}
    responses["/chat/completions"] = httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call]}, "finish_reason": "tool_calls"}],
    })
    history = [
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://x/cat.png"}}]},
        _assistant_calls(("c0", "get_weather", "{}", {"extra_content": {"google": {"thought_signature": "s"}}})),
        {"role": "tool", "tool_call_id": "c0", "content": "sunny"},
    ]

    result = await GroqProvider(api_key="k").generate(history, "m", {"tools": [_WEATHER_TOOL], "tool_choice": "auto"})

    payload = json.loads(requests[0].content)
    assert payload["tools"] == [_WEATHER_TOOL] and payload["tool_choice"] == "auto"
    assert payload["messages"][0] == history[0]
    assert "extra_content" not in payload["messages"][1]["tool_calls"][0]
    assert payload["messages"][2] == history[2]
    assert (result.content, result.tool_calls, result.finish_reason) == ("", [call], "tool_calls")


async def test_openai_compatible_stream_passes_tool_call_deltas(mock_http):
    _, responses = mock_http
    deltas = [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c", "function": {"name": "f", "arguments": ""}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
    ]
    responses["/chat/completions"] = httpx.Response(
        200, text="".join(f"data: {json.dumps(d)}\n\n" for d in deltas) + "data: [DONE]\n\n"
    )

    events = [e async for e in GroqProvider(api_key="k").generate_stream([{"role": "user", "content": "q"}], "m")]

    assert [e.tool_calls for e in events] == [
        deltas[0]["choices"][0]["delta"]["tool_calls"], deltas[1]["choices"][0]["delta"]["tool_calls"], None
    ]
    assert events[-1].finish_reason == "tool_calls"


async def test_gemini_candidate_without_parts_is_a_length_finish_not_an_outage(mock_http):
    # thinking models can use up max_tokens before writing any part
    _, responses = mock_http
    responses[":generateContent"] = httpx.Response(200, json={"candidates": [{
        "content": {"role": "model"}, "finishReason": "MAX_TOKENS",
    }]})
    result = await GeminiProvider(api_key="k").generate([{"role": "user", "content": "q"}], "m")
    assert (result.content, result.finish_reason) == ("", "length")


def test_provider_key_from_any_env_setting(monkeypatch):
    from app.config import Settings
    from app.router.model_router import provider_api_key

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deep")
    settings = Settings(_env_file=None, groq_api_key="gsk-1")
    assert provider_api_key(settings, "groq_api_key") == "gsk-1"
    assert provider_api_key(settings, "deepseek_api_key") == "sk-deep"
    assert provider_api_key(settings, "missing_api_key") == ""
    assert provider_api_key(settings, None) == ""
