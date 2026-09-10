# LLM Gateway

A lightweight, OpenAI-compatible-style LLM gateway that provides a single unified
API to multiple LLM providers while keeping conversation memory in the gateway
itself, independent of any one provider. If your primary provider is rate-limited
or down, the gateway falls back to another one automatically, mid-conversation,
without the client ever having to resend earlier messages.

This is a deliberately small, educational V1 - not a LiteLLM clone. The goal is to
make the core idea obvious and easy to explain end-to-end: **one API, many
providers, one shared memory, automatic fallback.**

## Providers in this build

The spec calls for Gemini, an OpenAI-compatible provider, and a local Ollama
model. This build ships **Gemini** and **Groq** (Groq exposes an
OpenAI-compatible chat completions API, so it fills that "OpenAI-compatible
provider" role directly). Ollama isn't wired up yet, but nothing about the
architecture is Gemini/Groq-specific - see "Adding a new provider" below for
exactly what adding Ollama (or real OpenAI, or anything else) would take.

## Architecture

```text
Client
   |
   v
FastAPI Gateway            (app/api/*.py)
   |
   v
Conversation Manager        (app/memory/conversation_manager.py)
   |
   v
Shared Memory  <----------  SQLite: conversations, messages, memories
   |
   v
Context Builder              (app/memory/context_builder.py)
   |
   v
Model Router                 (app/router/model_router.py)
   |
   v
LLM Provider  --- Gemini | Groq
   |
   v
Response
```

The conversation belongs to the **gateway's database**, not to any provider:

```text
                 Gateway Database
                       |
                Conversation
                       |
          +------------+------------+
          v                         v
       Gemini                     Groq
```

Whichever provider answers a given turn, the conversation itself lives in
SQLite. That's what makes fallback transparent: if Gemini returns a 429 or a
5xx, the gateway retries the same request context on Groq, and the client sees
one continuous conversation.

## Request flow

Every `POST /v1/chat/completions` call goes through the same pipeline:

```text
Request
  -> Conversation Manager   (load conversation, recent messages, summary)
  -> User Memories          (load stored facts for this user)
  -> Context Builder        (combine summary + memories + recent history + new message)
  -> Model Router           (pick a provider; "auto" tries a fallback chain)
  -> Provider                (Gemini or Groq, converted to their native request format)
  -> Store                  (save the user message and the assistant reply)
  -> Response                (normalized shape, regardless of which provider answered)
```

Concretely, in `app/api/chat.py`:

1. Load the conversation (404 if it doesn't exist).
2. Load its recent messages, stored summary, and the user's long-term memories.
3. Build the context to send to a provider.
4. Route to a provider (explicit model name, or `"auto"` with fallback).
5. Call the provider.
6. Store the user's message.
7. Store the assistant's reply.
8. Return a normalized response.

## Provider fallback

```text
model="auto"
      |
      v
  Gemini
      |
      +-- success --> return
      |
      +-- 429 / 5xx / timeout / connection error
                |
                v
              Groq
                |
                +-- success --> return
                |
                +-- unavailable --> 503 to the client
```

Fallback only triggers on **availability** failures - a custom
`ProviderUnavailableError` raised for 429/500/502/503/504, timeouts, and
connection errors. Normal client errors (400/401/403) propagate immediately and
are never retried on another provider, so a real mistake in the request never
gets silently masked by a fallback attempt.

An explicit `model="gemini"` or `model="groq"` request calls that provider only
- no fallback. Fallback is purely an `"auto"` behavior.

## Memory

Every request's context is built from three layers (`app/memory/context_builder.py`):

```text
Conversation summary   (produced by the summarizer once a conversation gets long)
        +
User memories           (long-term facts stored via POST /v1/memories)
        +
Recent messages         (the last MAX_RECENT_MESSAGES = 10 messages)
        +
Current user message
```

The full conversation is never resent on every request. Instead:

- Only the last 10 messages are loaded as-is (`MAX_RECENT_MESSAGES` in
  `conversation_manager.py`).
- Once a conversation crosses 20 stored messages, `app/memory/summarizer.py`
  asks whichever provider is currently available to produce a short summary of
  the whole conversation, which is then included alongside the recent window.
  This repeats every 20 messages, so nothing important falls out of context
  just because it scrolled past the recent window.
- User memories are a flat list of facts per user (no embeddings, no semantic
  search in V1) - included verbatim in every request for that user.

## Why this architecture?

- **Provider abstraction** (`app/providers/base.py`): the rest of the app only
  ever depends on the `LLMProvider` interface, never a concrete provider class.
  Adding a new provider means writing one adapter file - nothing else changes.
- **Centralized memory**: conversations are owned by the gateway's own
  database, not by any provider's session/thread concept. That's what makes
  switching providers mid-conversation possible at all.
- **Fallback, not a special case**: `"auto"` routing and fallback both live in
  one small `ModelRouter` class, driven entirely by whether a provider raises
  `ProviderUnavailableError` - no per-provider special-casing anywhere else.
- **Normalized responses**: every `/v1/chat/completions` response has the same
  shape (`id`, `conversation_id`, `model`, `provider`, `message`) no matter
  which provider actually generated it.

## Adding a new provider

1. Implement `LLMProvider` (`app/providers/base.py`) in a new file, e.g.
   `app/providers/ollama.py`.
2. Register it in `build_default_providers()` in `app/router/model_router.py`.
3. Optionally add it to `AUTO_FALLBACK_ORDER`.

Nothing in the API layer, conversation manager, or context builder needs to
change.

## Project structure

```text
app/
  main.py                       FastAPI app, startup, router wiring
  config.py                     Settings loaded from environment/.env
  api/
    conversations.py            POST /v1/conversations, GET .../{id}, GET .../{id}/messages
    chat.py                     POST /v1/chat/completions
    memories.py                 POST /v1/memories, GET /v1/memories/{user_id}
  providers/
    base.py                     LLMProvider interface, ProviderUnavailableError
    gemini.py                   Gemini adapter
    groq.py                     Groq (OpenAI-compatible) adapter
  router/
    model_router.py             Explicit routing + "auto" fallback chain
  memory/
    conversation_manager.py     Conversation/message CRUD (DB only, no provider logic)
    context_builder.py          Combines summary + memories + history into one prompt
    summarizer.py                Provider-backed conversation summarization
  database/
    database.py                 SQLAlchemy engine/session/init_db
    models.py                   Conversation, Message, Memory tables
    repository.py               Plain CRUD functions
  schemas/
    chat.py                     Pydantic request/response models for chat
tests/                          pytest suite (mocked providers, isolated in-memory DB)
scripts/
  demo.py                       End-to-end CLI walkthrough, including a forced fallback
```

## Running locally

```bash
cd llm-gateway            # wherever you cloned this repo
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt

cp .env.example .env
# then edit .env and fill in GEMINI_API_KEY and/or GROQ_API_KEY

python -m uvicorn app.main:app --reload
```

The API is now at `http://127.0.0.1:8000`, with interactive docs at
`http://127.0.0.1:8000/docs`.

Run the CLI demo (creates a conversation, chats, forces a simulated provider
outage, and shows the conversation survives it):

```bash
python scripts/demo.py
```

## API examples

Create a conversation:

```bash
curl -X POST http://127.0.0.1:8000/v1/conversations \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user_123"}'
```

Send a chat message (`model` can be `"auto"`, `"gemini"`, or `"groq"`):

```bash
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "conv_123",
    "model": "auto",
    "messages": [
      {"role": "user", "content": "I am building a Kafka based fraud detection system."}
    ]
  }'
```

Fetch a conversation and its full message history:

```bash
curl http://127.0.0.1:8000/v1/conversations/conv_123
curl http://127.0.0.1:8000/v1/conversations/conv_123/messages
```

Store a long-term memory for a user:

```bash
curl -X POST http://127.0.0.1:8000/v1/memories \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user_123", "content": "Prefers Python examples."}'

curl http://127.0.0.1:8000/v1/memories/user_123
```

Health checks:

```bash
curl http://127.0.0.1:8000/health
```

## Testing

```bash
python -m pytest
```

The suite (`tests/`) uses fake providers and an isolated in-memory SQLite
database for every test - it never makes a real network call and never needs
real API keys.

- `tests/test_router.py` - explicit routing, `"auto"` fallback, all-providers-down.
- `tests/test_memory.py` - conversation/message CRUD, memories, context building.
- `tests/test_chat.py` - the chat and conversation HTTP endpoints, including fallback and error responses.

## Docker

```bash
docker build -t llm-gateway .
docker run -p 8000:8000 --env-file .env llm-gateway
```

Configuration is supplied entirely at run time via environment variables -
nothing is baked into the image.

## Future improvements

Explicitly out of scope for this V1, listed here as future work only:

- Redis caching
- Rate limiting
- Streaming responses
- PostgreSQL
- Vector memory / semantic retrieval for user memories
- Kafka
- Prometheus/Grafana
- Intelligent (ML-based) model routing
- Ollama / local-model provider
