# Feature verification

Compared the two supplied attachments with the current working tree, README, CODEBASE_WALKTHROUGH, and `docs/*`. **Implemented** means present and functionally supported by inspected source/offline tests; it does not mean hardened. **Partial** identifies a material contract gap. **Unverified** means source/configuration exists but operational claims were not independently reproduced. Confidence is high for source/offline observations, medium for inferred operational impacts.

| Feature | Claimed status | Actual status | Evidence | Missing work | Priority |
|---|---|---|---|---|---|
| Stateless chat | Complete, SDK-compatible | Implemented with compatibility limits | `app/api/chat.py:84`, `app/schemas/chat.py:71`; `tests/test_openai_compat.py:24` | SDK contract testing; upstream status mapping; allow tool-only continuation if required | P1 |
| Stateful text conversations | Complete | Partial | `app/api/chat.py:142`, `app/memory/conversation_manager.py:33` | Atomic exchanges, ordering, retries/idempotency, bounded context | P0/P1 |
| SSE text/tool streaming | Complete | Partial | `app/api/chat.py:243`, `app/router/model_router.py:441`; stream probe | Explicit failure outcome; accurate stream usage; total duration/output cap | P1 |
| Tool calls/content parts/finish reasons | Complete stateless | Implemented offline | `app/providers/gemini.py:173,301,325`, `app/providers/openai_compatible.py:88`; `tests/test_providers.py:160` | Live provider round trips; parallel-tool policy, malformed tool validation | P1 |
| Unsupported features rejected | n>1/developer/stateful images/tools rejected | Partial | `app/api/chat.py:179`, `app/schemas/chat.py:38,99` | Unknown fields are ignored; n<=0 accepted; stateful tool history can be silently ignored | P1 |
| Embeddings float/base64 | Complete | Implemented offline | `app/api/embeddings.py:52,99`; `tests/test_gateway_api.py:500,590` | Vector cardinality/dimension validation, real adapters; missing usage handling | P1 |
| Model listing | Aliases/providers | Implemented | `app/api/models.py:11`, `app/router/model_router.py:167` | Filter by key if needed; list explicit model capability metadata | P2 |
| Native Gemini | Tools, embeddings, images | Implemented adapter; live behavior unverified | `app/providers/gemini.py:57`; transport tests | Live round trip; pinned models; embedding limits | P1 |
| Groq/OpenAI-compatible adapter | Groq enabled, others config-only | Implemented generic adapter; other services unverified | `app/providers/openai_compatible.py:50`, `gateway.toml:27` | Conformance matrix for each promised provider/model | P1 |
| Ollama/OpenAI/vLLM/NVIDIA deployment | One config block | Missing active integrations, reusable adapter present | `gateway.toml:35-48`, `app/router/model_router.py:498` | Supported config + real verification; custom key fields require Settings support | P2 |
| Mock provider | Keyless/load test | Implemented, intentionally synthetic | `app/providers/mock.py:24`, `loadtest/gateway.mock.toml` | Deterministic benchmark seed; no production inference quality claim | P2 |
| Aliases/direct pinning | Complete | Implemented | `app/router/model_router.py:192`; resilience tests | Validate targets at startup | P1 |
| Priority/latency/cost/load | Complete | Implemented heuristics | `app/router/model_router.py:212` | Per-model statistics if useful; cost strategy ignores prompt/output mix | P2 |
| Retry/backoff/429 fallback | Complete | Implemented | `app/router/model_router.py:267,301`, `app/gateway/retry.py:6` | Clarify ambiguous-delivery billing; Retry-After/cooldown policy | P1 |
| Circuit breaker | Per provider | Implemented local state | `app/gateway/health.py:53` | Concurrent state-transition testing; sharing optional | P2 |
| Request/attempt/idle timeouts | Complete | Partial whole-request guarantee | `app/router/model_router.py:228,377`; `app/api/chat.py:232,504` | Repair/summary create new route deadlines; streams unbounded after first event | P1 |
| Disconnect cancellation | Upstream closed/accounted | Implemented streaming cleanup offline | `app/router/model_router.py:290`; `tests/test_resilience.py:349`, `tests/test_gateway_api.py:564` | Real socket disconnect tests; non-stream cancellation outcome; unknown billed tokens | P1 |
| Hashed/revocable keys | Complete | Implemented | `app/auth.py:51,82,105`; `tests/test_gateway_api.py:70,94` | Rotation/expiry policy if needed; admin roles if multiple operators | P1 |
| Project isolation | Leak fixed | Implemented and tested between projects | `app/memory/conversation_manager.py:33`, `app/database/repository.py:140`; `tests/test_gateway_api.py:106` | End-user isolation is absent by design; decide trust boundary | P0 decision |
| Per-key model/RPM/TPM/budget | Complete, local with lag | Partial enforcement | `app/api/common.py:50`, `app/gateway/rate_limit.py:36` | In-flight reservations; unknown spend; shared workers; history pruning | P0/P2 |
| Secure defaults | Auth on/UI off/admin required | Implemented | `app/config.py:28`, `app/main.py:31`; startup test | Safe console exposure and deployment TLS | P0 conditional |
| Secret/PII/injection guardrails | Complete heuristics | Partial | `app/gateway/guardrails.py:186`, `app/api/chat.py:440`; memory probe | Stored context, tool definitions/arguments, schema text, images policy | P0/P1 |
| Output redaction | Nonstream enforced; streams post-check | Partial | `app/api/chat.py:130,379`; final-schema probe | Final payload validation; tool args policy; strict streaming policy | P1 |
| JSON/schema/repair | Complete nonstream | Partial | `app/gateway/structured.py:25,46`, `app/api/chat.py:208` | Offline refs, bounded CPU, final validation, multi-provider repair accounting | P0/P1 |
| Exact cache | Scoped TTL/LRU; off default | Implemented with policy gaps | `app/gateway/cache.py:23`, `app/api/chat.py:101` | Generated tool arguments may be sensitive; config/model revision keys; byte cap | P1/P2 |
| Conversation summaries | Bounded/best effort | Partial memory preservation | `app/memory/summarizer.py:29,36` | Old summary not fed back; all rows loaded before truncation; cost/ACL tracking | P1 |
| Usage row on every request | Complete background batches | Partial/lossy | `app/gateway/usage.py:63,203`; duplicate batch probe | Unique IDs, retry/durability, denied/auth/validation logs, per-attempt spend | P0/P1 |
| 16 Prometheus metrics | Complete | Verified definitions and endpoint test | `app/observability.py:24-62` | Writer/pool/loop metrics; label bound; incomplete streams currently success | P1 |
| Provisioned Grafana | Complete | Configured; running stack unverified now | `monitoring/grafana/dashboards/twinlm.json`, provisioning YAML | Startup and query validation; alerts/runbooks | P1 |
| Admin APIs/console | Admin API complete, no admin UI beyond Streamlit | Implemented | `app/api/admin.py:18`, `ui/streamlit_app.py:58,101` | Restrict exposure, token destination, pagination | P0/P1 |
| Database migration | Additive patch | Partial | `app/database/database.py:23,50`; old-schema test | Versioned serialized migrations; old index/backfill verification | P1 |
| Non-root Docker/Compose | Complete | Source verified; Compose syntax passes | `Dockerfile:17`, `docker-compose.yml:34` | Build/runtime smoke, backup, resources, TLS | P1 |
| CI/GHCR | Tests + image publishing | Workflow present, execution unverified | `.github/workflows/ci.yml:9,21` | Run evidence, scans, PostgreSQL/smoke gates, immutable locks | P1 |
| 198 offline tests ~5s | Passing | Verified 198 pass in 6.17s | Audit command in evidence report | Production-path and missing-case tests | P1 |
| ~200 RPS, 0 errors through 100 users | Historical measurement | Documented, not independently reproduced | `docs/PERFORMANCE.md:1` | Raw artifacts, isolated client/server, production DB/workloads | P2 |
| RAG/semantic cache/agent execution | Not claimed/deferred | Missing / not applicable to current product | No ingestion/vector/retrieval/executor module; provider interface only | Add only for demonstrated need | P3 |

## Documentation drift and undocumented details

`docs/FINAL_IMPLEMENTATION_REPORT.md:85,133` still describes 162 tests, auth off, missing tools, and stop-only finish reasons. Current code and 198 passing tests contradict those statements. `README.md` Testing and `docs/HOW_IT_WORKS.md` also retain 162. CODEBASE_WALKTHROUGH cites old snippets/line numbers and an older provider/router design. Keep historical reports explicitly historical; maintain one current support matrix.

Claims that every request produces a row exclude authentication/validation failures before handler creation and lossy background writes. Claims of a 0.5-second budget exposure exclude provider execution time, backlog and permanent write failures. Claims of startup config validation conflict with the successful missing-file health/readiness probe. Native Gemini parallel_tool_calls is accepted at the gateway but not mapped in `_build_payload`.

Deletion of conversations and full message-history reads exist (`app/api/conversations.py:82,96`) although the supplied summary only lists endpoint families. Streamlit offers admin/key/burst tabs; `ollama_base_url` is unused (`app/config.py:20`, factory uses TOML). `GroqProvider` is a compatibility wrapper used by scripts/tests, not dead code. Empty package initializers are normal. No TODO/mock evidence suggests production inference is secretly stubbed: mock is explicit configuration. No agent executor, vector store, Kafka, or Redis implementation was found.
