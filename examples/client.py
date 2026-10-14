"""Calling TwinLM with the official OpenAI SDK - the client never needs to
know which provider answers.

    pip install openai
    GATEWAY_API_KEY=gw_live_... python examples/client.py

GATEWAY_URL defaults to http://localhost:8000/v1. With AUTH_ENABLED=false
any api_key string works.
"""
import os

from openai import APIStatusError, OpenAI

client = OpenAI(
    base_url=os.environ.get("GATEWAY_URL", "http://localhost:8000/v1"),
    api_key=os.environ.get("GATEWAY_API_KEY", "gw_test_key"),
)

print("Models:", [m.id for m in client.models.list()])

# 1. Plain completion. "smart" is an alias from gateway.toml; the gateway
#    picks the provider and falls back if it is unavailable.
response = client.chat.completions.create(
    model="smart",
    messages=[{"role": "user", "content": "Explain distributed systems in two sentences."}],
    temperature=0.3,
)
print("\n[chat]", response.choices[0].message.content)
print("usage:", response.usage)

# 2. Streaming.
print("\n[stream] ", end="", flush=True)
stream = client.chat.completions.create(
    model="smart",
    stream=True,
    messages=[{"role": "user", "content": "Give me three tips for designing retries."}],
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="", flush=True)
print()

# 3. Structured output, validated by the gateway against the schema. If the
#    model's JSON is still invalid after a repair attempt, the gateway answers
#    502 instead of passing bad JSON through. (The mock providers in
#    loadtest/gateway.mock.toml never produce JSON, so they always hit this.)
try:
    response = client.chat.completions.create(
        model="smart",
        messages=[{"role": "user", "content": "Name a city in India and its approximate population."}],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "city",
                "schema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}, "population": {"type": "integer"}},
                    "required": ["city", "population"],
                },
            },
        },
    )
    print("\n[json]", response.choices[0].message.content)
except APIStatusError as exc:
    print(f"\n[json] rejected by the gateway ({exc.status_code}): {exc.body}")

# 4. Stateful mode (TwinLM extension): send only the new turn plus a
#    conversation_id; the gateway supplies the history, so a provider switch
#    mid-conversation keeps the context.
conversation = client.post("/conversations", body={"user_id": "example"}, cast_to=dict)
for turn in ["I'm building a Kafka-based fraud detection system.", "What am I building?"]:
    response = client.chat.completions.create(
        model="smart",
        messages=[{"role": "user", "content": turn}],
        extra_body={"conversation_id": conversation["conversation_id"]},
    )
    print(f"\n[memory] {turn}\n  -> {response.choices[0].message.content}")
