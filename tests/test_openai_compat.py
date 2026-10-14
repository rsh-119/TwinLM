"""OpenAI-compatible surface: stateless chat completions (no
`conversation_id`), OpenAI response/chunk shapes, `/v1/models`, `/ready`."""
import json

from sqlalchemy import text

from app.providers.base import GenerationResult, LLMProvider, TokenUsage
from app.router.model_router import ModelRouter
from tests.conftest import build_services


def _parse_sse(body):
    """Parsed JSON events of an SSE body, skipping OpenAI's `[DONE]` sentinel."""
    events = []
    for raw_event in body.split("\n\n"):
        data = "".join(
            line[len("data:"):].strip() for line in raw_event.splitlines() if line.startswith("data:")
        )
        if data and data != "[DONE]":
            events.append(json.loads(data))
    return events


def test_stateless_request_without_conversation_id_is_openai_compatible(client, fake_providers):
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "auto",
            "messages": [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Explain distributed systems"},
            ],
            "temperature": 0.2,
            "max_tokens": 50,
            "n": 1,  # unsupported OpenAI fields are ignored, not rejected
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "chat.completion"
    assert isinstance(body["created"], int)
    assert body["conversation_id"] is None
    assert body["choices"] == [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "hello from gemini"},
            "finish_reason": "stop",
        }
    ]
    assert body["message"] == body["choices"][0]["message"]
    assert fake_providers["gemini"].last_params == {"temperature": 0.2, "max_tokens": 50}


def test_stateless_request_sends_client_messages_as_is_and_stores_nothing(
    client, sqlite_engine, fake_providers
):
    seen = {}

    async def capture(messages, model, params=None):
        seen["messages"] = messages
        return GenerationResult(content="ok")

    fake_providers["gemini"].generate = capture
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"},
    ]

    client.post("/v1/chat/completions", json={"messages": messages})

    assert seen["messages"] == messages
    with sqlite_engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM messages")).scalar() == 0


def test_stateless_request_without_user_message_returns_400(client):
    response = client.post(
        "/v1/chat/completions", json={"messages": [{"role": "system", "content": "x"}]}
    )

    assert response.status_code == 400


def test_out_of_range_sampling_param_returns_422(client):
    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}], "temperature": 5},
    )

    assert response.status_code == 422


def test_unbounded_generation_or_message_count_is_rejected(client):
    payload = {"messages": [{"role": "user", "content": "hi"}]}
    assert client.post("/v1/chat/completions", json={**payload, "n": 0}).status_code == 422
    assert client.post("/v1/chat/completions", json={**payload, "max_tokens": 32769}).status_code == 422
    assert client.post("/v1/chat/completions", json={"messages": payload["messages"] * 201}).status_code == 422


def test_stateful_response_also_carries_openai_fields(client, fake_providers):
    conversation_id = client.post("/v1/conversations", json={"user_id": "u"}).json()["conversation_id"]

    body = client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.5,
        },
    ).json()

    assert body["conversation_id"] == conversation_id
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "hello from gemini"
    assert fake_providers["gemini"].last_params == {"temperature": 0.5}


def test_stateless_stream_emits_openai_chunks_then_done_sentinel(client, fake_providers):
    fake_providers["gemini"].chunks = ["Hel", "lo"]

    response = client.post(
        "/v1/chat/completions",
        json={"stream": True, "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    assert response.text.rstrip().endswith("data: [DONE]")
    events = _parse_sse(response.text)
    assert all(e["object"] == "chat.completion.chunk" for e in events)
    assert len({e["id"] for e in events}) == 1
    assert events[0]["choices"][0]["delta"] == {"role": "assistant", "content": "Hel"}
    assert events[1]["choices"][0]["delta"] == {"content": "lo"}
    assert events[-1]["choices"][0]["finish_reason"] == "stop"
    assert "".join(e["choices"][0]["delta"].get("content", "") for e in events) == "Hello"


def test_stateful_stream_keeps_legacy_fields_and_has_no_done_sentinel(client):
    conversation_id = client.post("/v1/conversations", json={"user_id": "u"}).json()["conversation_id"]

    response = client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert "[DONE]" not in response.text
    events = _parse_sse(response.text)
    assert events[-1]["done"] is True
    assert "".join(e["delta"] for e in events) == "hello from gemini"


def test_list_models(client):
    response = client.get("/v1/models")

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert [m["id"] for m in body["data"]] == ["auto", "gemini", "groq"]


def test_ready_when_database_reachable(client):
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_returns_503_when_database_unreachable(client):
    from sqlalchemy.exc import OperationalError

    from app.database.database import get_db
    from app.main import app

    class BrokenSession:
        def execute(self, *args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("db down"))

    app.dependency_overrides[get_db] = lambda: BrokenSession()

    response = client.get("/ready")

    assert response.status_code == 503


# ---- tools, content parts, finish_reason, include_usage ---------------------

_TOOL = {"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}
_CALL = {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city": "Pune"}'}}


class _OneShotProvider(LLMProvider):
    """Implements only `generate`, so streaming goes through the base
    class's single-event wrapper."""

    name = "gemini"

    def __init__(self, result):
        self.result = result
        self.seen = None

    async def generate(self, messages, model, params=None):
        self.seen = (messages, params)
        return self.result


def _use(gateway, result):
    provider = _OneShotProvider(result)
    gateway["router"] = ModelRouter({"gemini": provider})
    return provider


def test_tool_calls_round_trip_in_stateless_mode(client, gateway):
    provider = _use(gateway, GenerationResult("", None, [_CALL], "tool_calls"))
    history = [
        {"role": "user", "content": [{"type": "text", "text": "Weather?"}]},
        {"role": "assistant", "content": None, "tool_calls": [_CALL]},
        {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
    ]

    response = client.post(
        "/v1/chat/completions",
        json={"messages": history, "tools": [_TOOL], "tool_choice": "auto", "parallel_tool_calls": False},
    )

    assert response.status_code == 200
    choice = response.json()["choices"][0]
    assert choice["message"] == {"role": "assistant", "content": None, "tool_calls": [_CALL]}
    assert choice["finish_reason"] == "tool_calls"
    messages, params = provider.seen
    assert messages == [history[0], {"role": "assistant", "tool_calls": [_CALL]}, history[2]]
    assert params == {"tools": [_TOOL], "tool_choice": "auto", "parallel_tool_calls": False}


def test_streamed_tool_calls_and_finish_reason(client, gateway):
    _use(gateway, GenerationResult("", None, [_CALL], "tool_calls"))

    response = client.post(
        "/v1/chat/completions",
        json={"stream": True, "messages": [{"role": "user", "content": "q"}], "tools": [_TOOL]},
    )

    events = _parse_sse(response.text)
    assert events[0]["choices"][0]["delta"]["tool_calls"] == [{"index": 0, **_CALL}]
    assert events[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_provider_finish_reason_is_surfaced(client, gateway):
    _use(gateway, GenerationResult("cut off", None, None, "length"))

    response = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "q"}]})

    assert response.json()["choices"][0]["finish_reason"] == "length"
    assert response.json()["choices"][0]["message"] == {"role": "assistant", "content": "cut off"}


def test_stream_include_usage_adds_a_final_usage_chunk(client, fake_providers):
    fake_providers["gemini"].usage = TokenUsage(3, 2, 5)

    response = client.post(
        "/v1/chat/completions",
        json={"stream": True, "stream_options": {"include_usage": True}, "messages": [{"role": "user", "content": "q"}]},
    )

    assert response.text.rstrip().endswith("data: [DONE]")
    last = _parse_sse(response.text)[-1]
    assert last["choices"] == []
    assert last["usage"] == {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}


def test_stream_without_include_usage_has_no_usage_chunk(client):
    response = client.post("/v1/chat/completions", json={"stream": True, "messages": [{"role": "user", "content": "q"}]})

    assert all(e["choices"] for e in _parse_sse(response.text))


def test_n_greater_than_one_is_rejected(client):
    response = client.post("/v1/chat/completions", json={"n": 2, "messages": [{"role": "user", "content": "q"}]})

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "n > 1 is not supported"


def test_stateful_mode_rejects_tools_and_images_but_accepts_text_parts(client, fake_providers):
    conversation_id = client.post("/v1/conversations", json={"user_id": "u"}).json()["conversation_id"]
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}

    def turn(content, **extra):
        body = {"conversation_id": conversation_id, "messages": [{"role": "user", "content": content}], **extra}
        return client.post("/v1/chat/completions", json=body)

    tools = turn("q", tools=[_TOOL])
    assert tools.status_code == 400 and "stateless" in tools.json()["detail"]
    assert turn([image]).status_code == 400
    assert turn("q", stream=True, tools=[_TOOL]).status_code == 400
    assert turn([{"type": "text", "text": "hello "}, {"type": "text", "text": "there"}]).status_code == 200
    stored = client.get(f"/v1/conversations/{conversation_id}/messages").json()
    assert stored[0]["content"] == "hello there"


def test_message_shape_validation(client):
    def post(message):
        return client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "q"}, message]})

    assert post({"role": "user", "content": None}).status_code == 422
    assert post({"role": "assistant", "content": None}).status_code == 422
    assert post({"role": "tool", "content": "x"}).status_code == 422
    assert post({"role": "user", "content": [{"type": "audio", "data": "x"}]}).status_code == 422


def test_guardrails_redact_text_parts_and_tool_results(client, gateway):
    provider = _use(gateway, GenerationResult("ok"))
    gateway["services"] = build_services(guardrails={"input": {"secrets": "redact"}})
    secret = "AKIAABCDEFGHIJKLMNOP"
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}

    client.post("/v1/chat/completions", json={"messages": [
        {"role": "user", "content": [{"type": "text", "text": f"key {secret}"}, image]},
        {"role": "assistant", "content": None, "tool_calls": [_CALL]},
        {"role": "tool", "tool_call_id": "call_1", "content": f"found {secret}"},
    ]})

    user, assistant, tool = provider.seen[0]
    assert user["content"] == [{"type": "text", "text": "key [REDACTED_AWS_ACCESS_KEY]"}, image]
    assert "content" not in assistant
    assert tool["content"] == "found [REDACTED_AWS_ACCESS_KEY]"


def test_generated_tool_calls_are_not_cached(client, gateway):
    _use(gateway, GenerationResult("", None, [_CALL], "tool_calls"))
    gateway["services"] = build_services(cache={"enabled": True})

    history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": None, "tool_calls": [_CALL]},  # null content must not trip the sensitivity scan
        {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
    ]

    def post(tools):
        return client.post("/v1/chat/completions", json={"messages": history, "tools": tools})

    assert post([_TOOL]).headers["X-Cache"] == "MISS"
    second = post([_TOOL])
    assert second.headers["X-Cache"] == "MISS"
    assert second.json()["choices"][0]["message"]["tool_calls"] == [_CALL]
    assert second.json()["choices"][0]["finish_reason"] == "tool_calls"
    other = {"type": "function", "function": {"name": "other"}}
    assert post([other]).headers["X-Cache"] == "MISS"


def test_structured_output_is_not_enforced_on_a_tool_call_turn(client, gateway):
    _use(gateway, GenerationResult("", None, [_CALL], "tool_calls"))

    response = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "q"}], "tools": [_TOOL], "response_format": {"type": "json_object"}},
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["tool_calls"] == [_CALL]
