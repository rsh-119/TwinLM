# Baseline

Recorded 2026-10-04, before any changes in this improvement effort.

## Environment

- Python 3.13.2 (local `.venv`); Docker image targets 3.11
- FastAPI 0.141.1, Pydantic 2.13.5, SQLAlchemy 2.0.52, httpx 0.28.1, pytest 9.1.1
- No real provider calls were made. All verification used fake providers or the in-process TestClient.

## Test suite

```
python -m pytest -q
76 passed, 2 warnings in 2.43s
```

| File | Tests | Covers |
|---|---|---|
| `tests/test_chat.py` | 21 | chat API (JSON + SSE), fallback, 400/404/502/503 mapping, usage, persistence, web stream endpoint |
| `tests/test_router.py` | 20 | explicit/auto routing, fallback rules, streaming fallback-before-first-chunk |
| `tests/test_memory.py` | 19 | conversation manager, repository, token sums, context builder truncation |
| `tests/test_formatting.py` | 12 | markdown rendering, XSS sanitization |
| `tests/test_providers.py` | 4 | Groq 413 TPM-limit detection helper |

Warnings are Starlette/anyio deprecation notices about the TestClient and are harmless.

**Not covered by tests:** provider HTTP layers (Gemini/Groq request and response
handling), summarizer, streaming client disconnect, `/health`, DB failure behavior,
concurrency.

## Endpoint probe (TestClient)

| Request | Result |
|---|---|
| `GET /health` | 200 `{"status":"ok"}` |
| `GET /ready` | 404 |
| `GET /v1/models` | 404 |
| `POST /v1/embeddings` | 404 |
| `GET /metrics` | 404 |
| `POST /v1/chat/completions` OpenAI shape (`model:"smart"`, no `conversation_id`) | 422, `conversation_id` required |
| Same with `temperature`, `max_tokens` | 422; these fields would be silently dropped even with a `conversation_id` |
| `messages[].role = "tool"` | 422 |
| Any response | no `X-Request-ID` header |

## Problems found (ordered by severity)

| # | Severity | Problem | Location |
|---|---|---|---|
| 1 | **High (security)** | Gemini API key is sent as a URL query param and logged at INFO by httpx on every request. It is also embedded in `HTTPStatusError` messages that `logger.exception` writes | `providers/gemini.py:49,80`, `main.py:13` |
| 2 | High (compat) | Not usable by the OpenAI SDK: `conversation_id` is required, the response has no `choices`, and the SSE format is custom | `schemas/chat.py`, `api/chat.py` |
| 3 | Medium (perf) | Blocking SQLAlchemy calls inside `async def` handlers stall the event loop under concurrency | `api/chat.py` |
| 4 | Medium (perf) | A new `httpx.AsyncClient` (and TLS handshake) is created per provider call | `providers/*.py` |
| 5 | Medium (reliability) | Summarization runs inline on the request path with an unbounded transcript and can exceed provider limits on long chats (failure is swallowed, so it degrades rather than breaks) | `api/chat.py:298`, `memory/summarizer.py` |
| 6 | Medium (reliability) | Timeouts are hardcoded at 30s, with no total deadline and no same-provider retry | `providers/*.py` |
| 7 | Medium (accounting) | On a streaming client disconnect, the partial reply and its usage are not persisted | `api/chat.py:227` |
| 8 | Low | `default_model`, `openai_api_key`, and `ollama_base_url` settings are defined but never read | `config.py` |
| 9 | Low | Stray `--- IGNORE ---` text and an inaccurate docstring | `memory/summarizer.py:1` |
| 10 | Low | Dockerfile runs as root, has no HEALTHCHECK, and keeps the SQLite DB on the container filesystem (lost on restart) | `Dockerfile` |
| 11 | Info | No auth on any route; the memories API exposes any `user_id` | `api/memories.py` |

## Not measured yet

No load measurements existed at baseline. They are now in `docs/PERFORMANCE.md`, including the deadlock the first load run exposed.

## Resolution of baseline problems

| # | Problem | Resolution |
|---|---|---|
| 1 | Gemini key logged via URL | Fixed: key sent as `x-goog-api-key` header; httpx URL logging silenced; log redaction pass on every line |
| 2 | Not OpenAI-compatible | Fixed: stateless mode, OpenAI response/chunk shapes, `/v1/models`, `/v1/embeddings`; verified with the `openai` SDK |
| 3 | Blocking DB in async handlers | Mitigated: connections released before provider awaits (this caused a real deadlock under load, see `PERFORMANCE.md`); usage writes batched off the event loop. DB access is still sync SQLAlchemy |
| 4 | New HTTP client per call | Fixed: one pooled `httpx.AsyncClient` per provider |
| 5 | Unbounded inline summarization | Fixed: transcript bounded to 24k chars. Still runs on the request path (best-effort, failures swallowed) |
| 6 | Hardcoded timeouts, no retry | Fixed: per-provider timeout, total deadline, bounded retry with jitter, circuit breaker |
| 7 | Disconnect loses accounting | Fixed: recorded as `client_disconnected`; upstream request closed. The partial reply is intentionally not stored in the conversation |
| 8 | Unused settings | Partly fixed: `default_model` now drives the request default and summarization; `openai_api_key` / `nvidia_api_key` are usable via `api_key_setting` in `gateway.toml`. `ollama_base_url` is still unused (base URLs are set in `gateway.toml`); kept so existing `.env` files stay valid |
| 9 | Stray docstring text | Fixed |
| 10 | Dockerfile root / no healthcheck / ephemeral DB | Fixed: non-root user, `HEALTHCHECK`, `/app/data` volume path, compose with Postgres |
| 11 | No auth | Fixed: gateway API keys (opt-in via `AUTH_ENABLED`) protect all `/v1` routes |
