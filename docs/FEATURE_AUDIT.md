# Feature Audit

Current state after the 2026-10-04 improvement pass. The original audit
of the inherited code (before any changes) is in `docs/BASELINE.md`.
Every status was verified by tests and, where noted, by running the
gateway: a live uvicorn server with the official `openai` SDK, and the
full `docker compose` stack.

Status key: **EXISTING** works as intended · **PARTIAL** works with stated
gaps · **MISSING** not implemented (by decision, see Action).
"Origin" says whether a feature was inherited, repaired, or added.

## API

| Feature | Status | Origin | Evidence | Tests | Action / notes |
|---|---|---|---|---|---|
| `POST /v1/chat/completions` (OpenAI-compatible) | EXISTING | repaired | `app/api/chat.py`, `app/schemas/chat.py`; verified with `openai` SDK | `test_openai_compat.py`, `test_chat.py` | Tools / `tool_choice` / `tool` role, content-part lists (text, `image_url`), provider `finish_reason`. `n>1` rejected with 400. Tools and images are stateless-only (400 with `conversation_id`) |
| Stateful mode (`conversation_id`) | EXISTING | inherited | `app/memory/*` | `test_memory.py`, `test_chat.py` | Kept as-is |
| SSE streaming | EXISTING | repaired | OpenAI chunk format + legacy fields; `[DONE]` in stateless mode | `test_openai_compat.py`, `test_router.py` | Streamed `tool_calls` deltas; `stream_options.include_usage` adds the final usage chunk |
| Client disconnect cancels upstream | EXISTING | repaired | explicit generator closing chain, shielded upstream close (`model_router._close`) | `test_resilience.py`, `test_gateway_api.py`; verified live (row `client_disconnected`) | Partial reply is not stored in the conversation |
| `GET /v1/models` | EXISTING | added | `app/api/models.py` | `test_openai_compat.py` | |
| `POST /v1/embeddings` | EXISTING | added | `app/api/embeddings.py`; float and base64 encodings | `test_gateway_api.py`; verified with SDK | Groq has no embeddings API (skipped by router) |
| `GET /health`, `GET /ready` | EXISTING | repaired / added | `app/main.py`; `/ready` checks DB | `test_openai_compat.py` | |
| Conversations and memories APIs | EXISTING | inherited | `app/api/conversations.py`, `memories.py` | yes | Now behind auth when enabled |
| Web UI | EXISTING | inherited | `app/web/` | `test_formatting.py`, `test_chat.py` | No login; off by default (`WEB_UI_ENABLED`) |

## Providers and routing

| Feature | Status | Origin | Evidence | Tests | Action / notes |
|---|---|---|---|---|---|
| Provider interface (`generate`, `generate_stream`, `embed`, `aclose`) | EXISTING | improved | `app/providers/base.py` | indirect + transport tests | `health_check` not added: health is derived from real traffic (circuit breaker) instead of synthetic probes |
| Gemini adapter | EXISTING | repaired | key moved from URL to header; pooled client; embeddings; tools mapped to `functionDeclarations` (`parametersJsonSchema`) / `toolConfig`, tool history to `functionCall` / `functionResponse` | `test_providers.py` (MockTransport) | Images only as base64 `data:` URLs (else 400); only `function` tools. Thought signatures round-trip in `tool_calls[].extra_content`; missing ones get Google's bypass value |
| OpenAI-compatible adapter (Groq, OpenAI, Ollama, NVIDIA, vLLM) | EXISTING | added (from Groq) | `app/providers/openai_compatible.py` | `test_providers.py`, `test_resilience.py` | Tools, tool messages and content parts forwarded natively (Gemini's `extra_content` stripped). Only Groq is enabled in `gateway.toml`; others are a config block (commented examples) |
| Mock provider | EXISTING | added | `app/providers/mock.py` | used by load tests | For load tests / key-less runs only |
| Model registry (aliases, targets, pricing) | EXISTING | added | `gateway.toml`, `app/gateway/config.py`, `ModelRouter.resolve` | `test_resilience.py` | |
| Routing strategies: priority / cost / latency / load | EXISTING | added | `ModelRouter._order` | `test_resilience.py` | Latency = EWMA of successful attempts; load = in-flight count |
| Capability routing | PARTIAL | added | expressed as aliases (`smart`, `fast`, `embed`) | | No automatic prompt classification; by design |
| Health-aware routing | EXISTING | added | circuit-open targets skipped | `test_resilience.py` | |
| Retries (bounded, exponential backoff, full jitter) | EXISTING | added | `app/gateway/retry.py` | `test_resilience.py` | 429s skip straight to fallback |
| Timeouts (per attempt, total deadline, stream idle) | EXISTING | added | `ModelRouter._attempt_timeout` | `test_resilience.py` | Deadline bounds time-to-first-token for streams |
| Fallback | EXISTING | inherited, generalized | config-driven per alias | `test_router.py`, `test_resilience.py` | Never on client errors |
| Circuit breaker | EXISTING | added | `app/gateway/health.py` | `test_resilience.py` | In-process (per instance) |
| Provider health tracking | EXISTING | added | `ProviderHealth.snapshot`, `/admin/providers`, `llm_provider_health` | yes | |
| Malformed provider output | EXISTING | repaired | malformed JSON became 400 "unknown model"; now treated as an availability failure | `test_resilience.py` | |

## Gateway infrastructure

| Feature | Status | Origin | Evidence | Tests | Action / notes |
|---|---|---|---|---|---|
| API-key auth (`gw_live_…`, SHA-256 at rest) | EXISTING | added | `app/auth.py` | `test_gateway_api.py` | On by default; startup fails without `ADMIN_TOKEN`. Conversations and memories are scoped per project |
| Key scoping: project, allowed models, RPM/TPM, daily budget | EXISTING | added | `app/api/common.py::admit` | `test_gateway_api.py` | |
| Rate limiting (sliding window RPM + TPM) | EXISTING | added | `app/gateway/rate_limit.py` | `test_gateway_api.py` | In-process; per-provider/model limits not implemented (provider 429s are handled by fallback) |
| Usage tracking | EXISTING | added | `request_logs` table, `UsageWriter` (batched) | `test_gateway_api.py` | Rows lag up to 0.5 s |
| Cost tracking | EXISTING | added | `app/gateway/pricing.py`, prices in `gateway.toml` | `test_gateway_api.py` | Example prices; unknown model = null cost, not 0 |
| Exact-match response cache | EXISTING | added | `app/gateway/cache.py` | `test_gateway_api.py` | In-process LRU+TTL; stateless, non-stream, non-sensitive only; off by default |
| Semantic cache | MISSING | — | — | — | Deferred: needs an embedding call per request and a vector store, and has false-positive risk |
| Guardrails (secrets, PII, prompt injection, size) | EXISTING | added | `app/gateway/guardrails.py` | `test_gateway_api.py` | Regex/heuristic; scans text parts and tool results, not images or tool-call arguments; streamed output can only be logged, not blocked |
| Structured output (`json_object`, `json_schema`) | EXISTING | added | `app/gateway/structured.py` | `test_gateway_api.py` | Instruction + validation + bounded repair; not with `stream=true`; skipped on a turn where the model calls tools |
| Batch / queue execution | MISSING | — | — | — | Deferred: no use case yet |

## Observability and operations

| Feature | Status | Origin | Evidence | Tests | Action / notes |
|---|---|---|---|---|---|
| Request ID (`X-Request-ID`, in logs and `request_logs`) | EXISTING | repaired | `RequestContextMiddleware` | `test_gateway_api.py` | |
| Structured JSON logs with secret redaction | EXISTING | added | `app/observability.py` | `test_gateway_api.py` | httpx URL logging silenced |
| Prometheus metrics (`/metrics`) | EXISTING | added | 16 metrics, `app/observability.py` | `test_gateway_api.py`; scraped in compose | |
| Grafana dashboard | EXISTING | added | `monitoring/grafana/` | verified provisioned in compose | |
| OpenTelemetry tracing | MISSING | — | — | — | Deferred: single service, so request-ID correlation covers it; adds 3+ deps |
| Admin API | EXISTING | added | `app/api/admin.py` | `test_gateway_api.py` | `ADMIN_TOKEN`; no admin UI |
| Config system | EXISTING | added | `gateway.toml` (behavior) + env (secrets) | `test_gateway_api.py` | Validated at startup |
| Docker / compose | EXISTING | repaired | non-root image, HEALTHCHECK; compose with Postgres, Prometheus, Grafana | verified with `docker compose up` | Redis/Qdrant not included (unused) |
| Load testing | EXISTING | added | `loadtest/run.py` | results in `docs/PERFORMANCE.md` | |
| Summarizer | EXISTING | repaired | bounded transcript, uses `DEFAULT_MODEL` | | |
