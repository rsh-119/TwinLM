"""API-level tests: create a conversation, chat, retrieve it back.

Uses the `client` fixture from conftest.py, which wires the FastAPI app to
an isolated in-memory database and fake providers - no real network calls.
"""


def test_create_conversation(client):
    response = client.post("/v1/conversations", json={"user_id": "user_123"})

    assert response.status_code == 201
    body = response.json()
    assert body["conversation_id"].startswith("conv_")


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
