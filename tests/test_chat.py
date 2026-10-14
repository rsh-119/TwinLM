"""API-level tests: create a conversation, chat, retrieve it back. Uses the
`client` fixture (isolated in-memory DB + fake providers, conftest.py)."""
import json

from app.providers.base import TokenUsage


def _parse_sse(text):
    """Turn a raw `data: {...}\\n\\n` SSE body into a list of parsed events."""
    events = []
    for raw_event in text.split("\n\n"):
        lines = [line[len("data:"):].strip() for line in raw_event.splitlines() if line.startswith("data:")]
        if lines:
            events.append(json.loads("".join(lines)))
    return events


def test_create_conversation(client):
    response = client.post("/v1/conversations", json={"user_id": "user_123"})

    assert response.status_code == 201
    body = response.json()
    assert body["conversation_id"].startswith("conv_")


def test_message_history_uses_bounded_cursor_pages(client):
    conversation_id = client.post("/v1/conversations", json={"user_id": "u"}).json()["conversation_id"]
    for i in range(3):
        response = client.post("/v1/chat/completions", json={
            "conversation_id": conversation_id, "messages": [{"role": "user", "content": f"turn-{i}"}]
        })
        assert response.status_code == 200
    url = f"/v1/conversations/{conversation_id}/messages"
    newest = client.get(url, params={"limit": 2}).json()
    previous = client.get(url, params={"limit": 2, "before": newest[0]["id"]}).json()
    assert [m["content"] for m in newest] == ["turn-2", "hello from gemini"]
    assert [m["content"] for m in previous] == ["turn-1", "hello from gemini"]


def test_get_conversation_not_found(client):
    response = client.get("/v1/conversations/conv_does_not_exist")

    assert response.status_code == 404


def test_send_chat_completion_returns_normalized_response(client):
    conversation_id = client.post("/v1/conversations", json={"user_id": "user_123"}).json()[
        "conversation_id"
    ]

    response = client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "model": "auto",
            "messages": [{"role": "user", "content": "Explain Kafka partitioning."}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == conversation_id
    assert body["provider"] == "gemini"
    assert body["message"] == {"role": "assistant", "content": "hello from gemini"}
    assert body["id"].startswith("req_")


def test_chat_completion_unknown_conversation_returns_404(client):
    response = client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": "conv_does_not_exist",
            "model": "auto",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 404


def test_chat_completion_unknown_model_returns_400(client):
    conversation_id = client.post("/v1/conversations", json={"user_id": "user_123"}).json()[
        "conversation_id"
    ]

    response = client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "model": "not-a-real-provider",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 400


def test_chat_completion_no_user_message_returns_400(client):
    conversation_id = client.post("/v1/conversations", json={"user_id": "user_123"}).json()[
        "conversation_id"
    ]

    response = client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "model": "auto",
            "messages": [{"role": "assistant", "content": "hi"}],
        },
    )

    assert response.status_code == 400


def test_chat_completion_falls_back_when_primary_provider_is_unavailable(client, fake_providers):
    fake_providers["gemini"].behavior = "unavailable"
    conversation_id = client.post("/v1/conversations", json={"user_id": "user_123"}).json()[
        "conversation_id"
    ]

    response = client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "model": "auto",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["provider"] == "groq"


def test_chat_completion_all_providers_unavailable_returns_503(client, fake_providers):
    fake_providers["gemini"].behavior = "unavailable"
    fake_providers["groq"].behavior = "unavailable"
    conversation_id = client.post("/v1/conversations", json={"user_id": "user_123"}).json()[
        "conversation_id"
    ]

    response = client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "model": "auto",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 503


def test_chat_completion_reports_token_usage(client, fake_providers):
    fake_providers["gemini"].usage = TokenUsage(prompt_tokens=15, completion_tokens=9, total_tokens=24)
    conversation_id = client.post("/v1/conversations", json={"user_id": "user_123"}).json()[
        "conversation_id"
    ]

    response = client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "model": "auto",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.json()["usage"] == {"prompt_tokens": 15, "completion_tokens": 9, "total_tokens": 24}


def test_chat_completion_omits_usage_when_not_reported(client):
    conversation_id = client.post("/v1/conversations", json={"user_id": "user_123"}).json()[
        "conversation_id"
    ]

    response = client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "model": "auto",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.json()["usage"] is None


def test_stream_chat_completion_emits_sse_and_persists_the_full_reply(client, fake_providers):
    fake_providers["gemini"].chunks = ["hello ", "from ", "gemini"]
    conversation_id = client.post("/v1/conversations", json={"user_id": "user_123"}).json()[
        "conversation_id"
    ]

    response = client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "model": "auto",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    assert [e["delta"] for e in events] == ["hello ", "from ", "gemini", ""]
    assert events[-1]["done"] is True
    assert all(e["provider"] == "gemini" for e in events)

    messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "hello from gemini"
    assert messages[1]["model"] == "gemini"


def test_stream_chat_completion_unexpected_provider_error_returns_502_not_500(client, fake_providers):
    """Regression: gemini fails retryably (falls back, as designed), then
    groq fails with something that ISN'T ProviderUnavailableError (a real
    non-retryable error, or a bug parsing its response). This used to leak
    as an unhandled 500 in the streaming path even though the equivalent
    non-streaming request correctly gives a clean 502."""
    fake_providers["gemini"].behavior = "unavailable"
    fake_providers["groq"].behavior = "crash"
    conversation_id = client.post("/v1/conversations", json={"user_id": "user_123"}).json()[
        "conversation_id"
    ]

    response = client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "model": "auto",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 502


def test_stream_chat_completion_reports_usage_on_the_done_event_and_persists_it(client, fake_providers):
    fake_providers["gemini"].chunks = ["hello ", "from ", "gemini"]
    fake_providers["gemini"].usage = TokenUsage(prompt_tokens=11, completion_tokens=6, total_tokens=17)
    conversation_id = client.post("/v1/conversations", json={"user_id": "user_123"}).json()[
        "conversation_id"
    ]

    response = client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "model": "auto",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    events = _parse_sse(response.text)
    assert events[-1]["done"] is True
    assert events[-1]["usage"] == {"prompt_tokens": 11, "completion_tokens": 6, "total_tokens": 17}
    assert all("usage" not in e for e in events[:-1])

    messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()
    assert messages[1]["content"] == "hello from gemini"


def test_stream_chat_completion_unknown_conversation_returns_404(client):
    response = client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": "conv_does_not_exist",
            "model": "auto",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 404


def test_stream_chat_completion_all_providers_unavailable_returns_503(client, fake_providers):
    fake_providers["gemini"].behavior = "unavailable"
    fake_providers["groq"].behavior = "unavailable"
    conversation_id = client.post("/v1/conversations", json={"user_id": "user_123"}).json()[
        "conversation_id"
    ]

    response = client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "model": "auto",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )

    assert response.status_code == 503


def test_web_ui_stream_endpoint_unexpected_provider_error_returns_502_not_500(client, fake_providers):
    fake_providers["gemini"].behavior = "unavailable"
    fake_providers["groq"].behavior = "crash"
    conversation_id = client.post("/v1/conversations", json={"user_id": "user_123"}).json()[
        "conversation_id"
    ]

    response = client.post(
        f"/conversations/{conversation_id}/chat/stream",
        data={"content": "hi", "model": "auto"},
    )

    assert response.status_code == 502


def test_web_ui_stream_endpoint_renders_markdown_and_persists(client, fake_providers):
    fake_providers["gemini"].reply = "**bold** reply"
    conversation_id = client.post("/v1/conversations", json={"user_id": "user_123"}).json()[
        "conversation_id"
    ]

    response = client.post(
        f"/conversations/{conversation_id}/chat/stream",
        data={"content": "hi", "model": "auto"},
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[-1]["done"] is True
    assert any("<strong>bold</strong>" in e["html"] for e in events)

    messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()
    assert messages[1]["content"] == "**bold** reply"


def test_delete_conversation(client):
    conversation_id = client.post("/v1/conversations", json={"user_id": "user_123"}).json()[
        "conversation_id"
    ]

    response = client.delete(f"/v1/conversations/{conversation_id}")

    assert response.status_code == 204
    assert client.get(f"/v1/conversations/{conversation_id}").status_code == 404


def test_delete_conversation_not_found_returns_404(client):
    response = client.delete("/v1/conversations/conv_does_not_exist")

    assert response.status_code == 404


def test_conversation_and_messages_persist_across_turns(client):
    conversation_id = client.post("/v1/conversations", json={"user_id": "user_123"}).json()[
        "conversation_id"
    ]

    client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "model": "auto",
            "messages": [{"role": "user", "content": "first message"}],
        },
    )
    client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "model": "auto",
            "messages": [{"role": "user", "content": "second message"}],
        },
    )

    conversation = client.get(f"/v1/conversations/{conversation_id}").json()
    assert conversation["user_id"] == "user_123"

    messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[0]["content"] == "first message"
    assert messages[2]["content"] == "second message"


def test_conversation_total_tokens_accumulates_across_turns(client, fake_providers):
    fake_providers["gemini"].usage = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    conversation_id = client.post("/v1/conversations", json={"user_id": "user_123"}).json()[
        "conversation_id"
    ]

    client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "model": "auto",
            "messages": [{"role": "user", "content": "first"}],
        },
    )
    client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "model": "auto",
            "messages": [{"role": "user", "content": "second"}],
        },
    )

    conversation = client.get(f"/v1/conversations/{conversation_id}").json()
    assert conversation["total_tokens"] == 30

    messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()
    assistant_messages = [m for m in messages if m["role"] == "assistant"]
    assert all(m["total_tokens"] == 15 for m in assistant_messages)
    assert all(m["total_tokens"] is None for m in messages if m["role"] == "user")
