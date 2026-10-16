# TwinLM architecture

**Status:** working-tree architecture, revised 14 October 2026. This is the
single maintained architecture reference. **Current** sections describe
implemented and tested behavior; **Target** sections are decisions or work not
yet done. Operating procedures are in [OPERATIONS.md](OPERATIONS.md); a file
index for agents is in [CODEBASE_GRAPH.md](CODEBASE_GRAPH.md). The `audit/`
reports and dated reports in `docs/` are historical evidence, not a
description of this release.

## Purpose and design constraints

TwinLM is a lightweight, self-hostable LLM gateway for a small team or
business: one OpenAI-style API over several model providers, project-level
keys, limits and usage visibility, and optional gateway-owned conversations.
It aims to cut **avoidable** model and operating cost through explicit
routing, limits and visibility; savings must be measured against a real
baseline.

Shape: a modular monolith, one gateway process, one durable database.
Stateless proxying works without conversation storage. Shared coordination,
more services or more providers are added only when a measured workload needs
them. Correctness, isolation, recoverability and honest accounting come before
throughput.

## Current: system boundaries

```mermaid
flowchart LR
  Client[SDK / application] --> Ingress[Caddy TLS ingress<br/>optional profile]
  Ingress --> HTTP
  Client -. private host .-> HTTP[FastAPI gateway]
  Console[Web console /app<br/>Next.js, served by the gateway] --> HTTP
  HTTP --> Auth[API key / admission]
  Auth --> Proxy[Stateless chat + embeddings]
  Proxy --> Stateful[Optional stateful layer<br/>conversations, memories, summaries]
  Proxy --> Router[Model router<br/>groups, deployments, capabilities]
  Router --> Gemini[Gemini adapter]
  Router --> Compat[OpenAI-compatible adapter]
  Router --> Mock[Mock adapter]
  Stateful --> DB[(PostgreSQL / SQLite dev)]
  Proxy --> Outbox[(Local SQLite usage outbox)]
  Outbox --> DB
  HTTP --> Metrics[/metrics]
  Prometheus[Prometheus + alert rules<br/>optional profile] --> Metrics
```

`app/main.py` assembles one FastAPI app. `app/api/` owns HTTP contracts;
`app/api/stateful.py` is the only path into conversation/memory data;
`app/router/model_router.py` owns target selection and resilience;
`app/providers/` translates provider protocols; `app/database/` persists and
migrates. Provider HTTP clients are pooled and async; SQLAlchemy sessions are
synchronous and `release_connection` returns the connection before slow
provider awaits. Usage rows are written by a background thread from a local
outbox. `app/ops.py` is the operator CLI (migrate, verify, retention, outbox).

The gateway does remote inference orchestration only. It does not host or
train models, execute tool calls, provide RAG/vector search, or make different
providers' answers equivalent.

### Current: request lifecycle

1. Middleware assigns a server request ID, bounds body size and in-flight
   requests. Pydantic validates the schema; unknown request fields are
   rejected (below). `/v1` uses gateway API keys, `/admin` a separate token.
2. Admission: model allow-list, RPM/TPM windows, budgets (key, team, user,
   org): settled + locally pending + in-flight reserved cost + this request's
   worst case must stay within each limit; the worst case is reserved until
   the request's usage row is queued (`app/gateway/budgets.py`).
3. Stateless requests send the client's messages after input guardrails and
   touch no conversation data. Requests with `conversation_id` go through
   `app/api/stateful.py`: ownership check, summary + bounded recent window +
   capped memories, guardrails on the assembled context. Optional exact-match
   cache (off by default) applies only to eligible stateless non-streaming turns;
   for aliases in `[cache.semantic] models`, a miss embeds the last user message
   and may reuse an answer to a similar one (same earlier context).
4. The router resolves the model to **model groups of deployments**, drops
   deployments lacking a needed capability, orders by strategy, and tries
   each deployment with per-attempt/total timeouts and bounded retries
   (provider circuit breakers gate attempts). Next deployment in the same
   group is a retry of the same model; the next group is a **fallback**.
5. Non-streaming: optional JSON/schema validation and bounded repair, output
   guardrails, revalidation after redaction. Stateful turns commit both
   messages atomically with optimistic versioning (stale turn → 409); summaries
   every 20 messages are separate, billed requests.
6. A usage record (provider, model, **deployment**, tokens, cost or null,
   latency, status) goes to the outbox, then to `request_logs`; replay after
   restart deduplicates by request ID; poison rows go to a deadletter table.

Streaming retries/falls back only before the first chunk. Later failures end
the stream with an explicit incomplete outcome (HTTP status may already be
200). Output guardrails see streamed text only after delivery: a finding marks
the stream incomplete and prevents stateful persistence but cannot recall
bytes. Use non-streaming where pre-delivery blocking is required.

### Current: compatibility matrix (`/v1`)

| Feature | Status |
| --- | --- |
| `chat.completions` JSON and SSE, `stream_options.include_usage` | Supported; tested with the official OpenAI Python SDK (`tests/test_sdk_contract.py`) |
| `temperature`, `top_p`, `max_tokens` / `max_completion_tokens`, `stop` (≤4), `presence_penalty`, `frequency_penalty`, `seed` | Passed to the provider |
| `tools`, `tool_choice`, `parallel_tool_calls`; tool-call results | Stateless only; routed only to deployments with `tools`. Gemini ignores `parallel_tool_calls=false` |
| Image parts (`image_url`) | Stateless only; routed only to deployments with `vision`; Gemini needs `data:` URLs |
| `response_format` `json_object` / `json_schema` | Gateway-enforced validation + bounded repair; not with `stream=true` |
| `user` | Accepted, not forwarded |
| `n > 1`, `logprobs`, `top_logprobs`, `logit_bias`, `audio`, `modalities`, `prediction`, `store`, `metadata`, `functions`, any other field | **400** `unsupported_parameter` with `param`; explicit `null` values are tolerated |
| `embeddings` (float/base64) | Routed only to deployments with `embeddings`; `dimensions` → 400 |
| `models` | Models this key may use, with a TwinLM `capabilities` extension |
| `conversation_id`, `/v1/conversations`, `/v1/memories` | TwinLM extensions; off with `STATEFUL_ENABLED=false` |

Capabilities: `chat`, `tools`, `vision`, `embeddings`. Adapter defaults
(`DEFAULT_CAPABILITIES`) are narrowed per provider or per deployment in
`gateway.toml`. A request no deployment can serve gets a 400 naming the
missing capability; it is never silently dropped or sent to an incapable model.

### Current: deployments, groups and fallback

A **provider** in `gateway.toml` is one endpoint + credential and is the
circuit-breaker unit. A **deployment** is a named (provider, model) with its
own capabilities; unnamed `provider/model` targets get the id
`provider/model`. An alias lists either `targets` (each its own group:
classic ordered fallback) or `groups` (deployments of the same model, tried
before falling back). `routing_strategy` (priority/latency/cost/load) orders
deployments within a group and groups by their best member; `priority` keeps
config order. Pinning a deployment id, provider or `provider/model` means no
fallback. Deployment ids appear in `request_logs`, `/admin/usage?group_by=deployment`
and `/admin/models`.

### Current: identity, ownership and trust

| Principal | How | Can do |
| --- | --- | --- |
| Service key | `POST /admin/keys` without `user_id` | Any `/v1` call in its project; may act for any `user_id` in the project unless `STATEFUL_REQUIRES_USER_KEYS=true` |
| User-bound key | `POST /admin/keys` with `user_id` | Same, but conversations/memories only for that user (others: 404, explicit other `user_id`: 403) |
| Admin | `ADMIN_TOKEN` bearer | `/admin`: keys (create, rotate, revoke), usage, models, providers, cache |
| No auth | `AUTH_ENABLED=false` | Everything in the reserved `default` project; private single-user setups only |

Keys are 256-bit random; only SHA-256 hashes are stored. Validated keys are
cached in-process for `AUTH_CACHE_SECONDS` (30 s); admin revoke/rotate evicts
immediately, and an expired entry is reused only while the database is
unreachable. Logs store metadata
and tokens, never prompts; conversations and memories store user text.
Guardrails are heuristic. There is no SSO; user identity comes from which key
was issued to whom.

### Current: persistence and migrations

Tables: `conversations`, `messages`, `memories`, `api_keys`, `request_logs`,
console users/teams/sessions, admin-panel providers/aliases/prices, `budgets`,
`budget_alerts`, `schema_migrations`. Migrations are versioned, forward-only and idempotent,
applied at startup under a database lock (or by `python -m app.ops migrate`
with `MIGRATE_ON_STARTUP=false`); a newer-than-code schema is refused.
Upgrades from the v1 schema are tested on SQLite and PostgreSQL 16
(`tests/test_ops.py`). Rollback is restore-from-backup.

### Current: usage and cost

Costs are estimates: configured `provider/model` prices × provider-reported
tokens, summed over every billed call (including JSON repairs; summaries are
their own rows). Missing usage fields or prices are **unknown (null), never 0**;
`/admin/usage` reports `unpriced_requests` and `cost_complete`; cache hits cost
0 and are excluded from provider token totals. Gemini thinking tokens count as
output. A request's tokens are attributed to the deployment that served the
final answer, even if a repair call used another one; its cost is exact per
call. Budgets reserve each request's worst-case cost at admission, so they
hold for priced models with an output cap; unpriced calls reserve nothing.
Alerts (80%, 100% or first refusal) are recorded once per budget period in
`budget_alerts` (unique constraint, so once across instances) and sent to
`BUDGET_ALERT_WEBHOOK_URL`.

Cost controls: an output-token cap (`default_max_output_tokens`, per-alias
`max_output_tokens`) fills in or clamps `max_tokens`; the exact-match cache
serves only deterministic stateless requests (`temperature` unset or 0) with
an optional per-alias TTL; `/admin/savings` compares actual cost with the
same measured tokens priced at `[savings] baseline`.

**Product scope.** The core is the OpenAI-compatible API, keys with limits,
usage/cost, aliases with routing/fallback, the cache, output caps and the
savings report. Conversations/memories, guardrails, structured-output repair
and the UIs are optional and frozen: maintained, not extended, unless a user
needs them. New provider types are added only on demand; the
OpenAI-compatible adapter is the default integration path.

### Current: deployment

`docker compose up` starts only secrets, PostgreSQL and the gateway. Profiles
add `console`, `monitoring` (Prometheus + `monitoring/alerts.yml` + Grafana)
and `ingress` (Caddy: automatic TLS, only `/v1/*` public, basic auth on
operator UIs). Images are pinned by digest; Python dependencies are
hash-locked (`requirements.lock`, `requirements-dev.lock`,
compiled for Linux/Python 3.11; the console's npm lock in `console/`). Breakers, RPM/TPM
windows and the cache are process-local: **one gateway instance is the
supported baseline**. The PostgreSQL pool is persistent (`DB_POOL_SIZE`,
pre-ping, 3 s connect timeout). During a database outage, stateless chat with
already-validated keys keeps working (usage waits in the outbox); everything
that needs the database returns `503 database unavailable` with
`Retry-After`. Measured behavior: [PERFORMANCE.md](PERFORMANCE.md).

## Target: remaining design work

| Item | Decision / trigger |
| --- | --- |
| Budgets for unpriced models | Implemented for priced models: worst-case reservation at admission, release when the usage row is queued, Redis reservations expire after 15 min if an instance crashes. Unpriced models reserve nothing; provider spend limits remain their hard stop. Images and stateful context are not in the input estimate. |
| SSO / identity provider | Not implemented. **External input:** IdP choice. Then map IdP subjects to user-bound keys or front the gateway with an identity-aware proxy. |
| Multiple gateway instances | With `REDIS_URL` (`app/gateway/shared.py`): RPM/TPM windows, exact cache, budget reservations, key revocation and console config changes are shared; fails open when Redis is down. Breakers, latency stats, the semantic cache and the usage outbox stay per instance. Not benchmarked with several instances yet. |
| Per-deployment breakers | Breakers are per provider (endpoint + credential). A model-specific outage on one endpoint opens it for all its models. Split when that is observed. |
| Bulk user erasure | Per-conversation delete exists; add a user-level erase if a retention/GDPR policy requires it. |
| Alert delivery, off-host backups, retention schedule, TLS domains | Procedures and tooling exist ([OPERATIONS.md](OPERATIONS.md)); **external input** needed: receivers, backup destination/key, retention periods, DNS. |

### Design decisions and revisit triggers

| Decision | Reason now | Revisit when |
| --- | --- | --- |
| One modular FastAPI process | Small footprint, clear modules | Independent scaling/failure isolation has measured value |
| PostgreSQL for teams; SQLite for development | Durable relational records, no extra service type | Measured workload demands otherwise |
| Local breakers and semantic cache, Redis for the rest when set | Health is learned per instance; semantic cache needs no vector store | Breaker flapping across instances, or semantic cache too large for memory |
| Exact-match cache off by default; semantic cache opt-in per alias | No silent wrong answers by default | Representative traffic shows a safe hit rate |
| Inline summaries and JSON repair | Simpler operation | Tail latency or reliability warrants durable jobs |
| Own provider adapters | Small provider set | Maintenance cost justifies a translation library |
| Basic auth on operator UIs via Caddy | No extra service | SSO is required |

## Verification and acceptance gates

See the final section of [PERFORMANCE.md](PERFORMANCE.md) for the current
benchmark and `docs/CODEBASE_GRAPH.md` for the verification commands. Before
claiming business readiness for a specific deployment:

1. Live-provider contract tests and invoice reconciliation with non-production credentials.
2. A restore drill from an encrypted off-host backup on the target host.
3. TLS ingress on the real domain; alert delivery to a real receiver.
4. A retention policy configured and exercised.
5. A benchmark on the target hardware; release targets set from it.
