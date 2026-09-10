# TwinLM

A small FastAPI service that sits in front of multiple LLM providers (Gemini and
Groq, for now) behind one API. The gateway owns the conversation, not the
provider — so if Gemini is rate-limited or down, it falls back to Groq
mid-conversation and the user doesn't notice, because the conversation history
lives in the gateway's own database instead of in any provider's session.

This is a learning project, not a LiteLLM replacement. The goal was to keep it
small enough to read in one sitting and explain in an interview.

## How it works

```
Client -> FastAPI -> Conversation Manager -> Context Builder -> Model Router -> Provider
```

Every chat request loads the conversation's recent messages, its summary (once
it gets long), and any long-term memories for that user, combines all of that
into one prompt, and sends it to a provider. The user's message and the
provider's reply both get saved back to the conversation afterward.

For `model="auto"`, the router tries Gemini first and only falls back to Groq
on availability errors — 429, 5xx, timeouts, connection errors. A normal
400/401 is never retried on another provider, it just fails.

Providers implement one small interface (`LLMProvider.generate`), so adding
Ollama or a real OpenAI key later means writing one new file under
`app/providers/`, not touching the router or the API layer.

## Memory

Context sent to a provider is built from three things: the last 10 messages, a
running summary (regenerated every 20 messages), and a flat list of memories
per user, set via `/v1/memories`. No embeddings, no vector search — just
enough to keep a long conversation coherent without resending the whole thing
on every request.

## Running it

```bash
python -m venv .venv
.venv\Scripts\activate      # or: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in GEMINI_API_KEY / GROQ_API_KEY
python -m uvicorn app.main:app --reload
```

Docs at http://127.0.0.1:8000/docs.

For a quick walkthrough, run `python scripts/demo.py` — it creates a
conversation, chats a bit, forces a simulated provider outage, and shows the
conversation surviving the switch.

## API

```bash
curl -X POST http://localhost:8000/v1/conversations \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user_123"}'

curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "conv_123",
    "model": "auto",
    "messages": [{"role": "user", "content": "I am building a Kafka based fraud detection system."}]
  }'

curl http://localhost:8000/v1/conversations/conv_123/messages

# add "stream": true to get Server-Sent Events back instead of one JSON body
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "conv_123",
    "model": "auto",
    "stream": true,
    "messages": [{"role": "user", "content": "Give me a two-step retry plan."}]
  }'

curl -X POST http://localhost:8000/v1/memories \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user_123", "content": "Prefers Python examples."}'
```

`model` can be `"auto"`, `"gemini"`, or `"groq"`.

## Tests

```bash
python -m pytest
```

Everything runs against fake providers and an in-memory SQLite database — no
API keys needed, no network calls made.

## Docker

```bash
docker build -t twinlm .
docker run -p 8000:8000 --env-file .env twinlm
```

## Streaming

Set `"stream": true` on `/v1/chat/completions` (or just use the web UI) and
the response comes back as Server-Sent Events instead of one JSON blob -
`data: {"provider", "model", "delta", "done"}` lines, same fallback rules as
the non-streaming path. The web UI renders those deltas live, markdown and
all, the same way ChatGPT/Claude/Gemini show a reply as it's generated.

One limitation worth knowing: fallback (Gemini -> Groq) only happens *before*
the first token is sent - once a provider has started streaming, bytes are
already with the client, so a failure mid-response ends the stream (with a
short note appended) instead of silently switching providers.

## Not in this version

Redis, rate limiting, Postgres, vector memory, Kafka, Prometheus/Grafana,
smarter routing, and the Ollama provider are all left out on purpose - the
point of this version was to keep the core idea legible, not to build all of
that out too.
