# TwinLM

TwinLM is a small, OpenAI-compatible LLM gateway for cutting AI spend in an
existing setup. Point any OpenAI SDK or framework at it (change `base_url` and
`api_key`), and it shows where the money goes, sends each use case to the
cheapest model that is good enough, answers repeated requests from cache,
caps runaway outputs, and fails over when a provider is down.

**Core** (kept small and well tested): OpenAI-compatible API · one key per
app/team with limits · budgets per company, team and person with alerts ·
usage and cost per key/project/model · aliases with cheap-first routing and
fallback · exact-match and semantic cache · output-token caps · savings
report against a baseline model.
**Optional** (works, not the focus): gateway-owned conversations and
memories, guardrails, structured-output repair, web UI and test console.
Providers: one OpenAI-compatible adapter (OpenAI, Groq, DeepSeek, Together,
Mistral, Ollama, vLLM, ...) plus Gemini.

Budgets refuse a request whose worst-case cost would cross the limit, so
they hold as long as output is capped and prices are known; unpriced models
and provider billing differences can still go over. Verify prices before
relying on cost reports or budgets.

It started from a single principle that still sets it apart: **the gateway
can own the conversation, not the provider.** In stateful mode the client
sends only the new turn and a `conversation_id`. The history lives in the
gateway's database, so when a provider is rate-limited the conversation
continues on another provider with no visible change to the client.

**New here?** Start with the [architecture](docs/ARCHITECTURE.md) (implemented vs. proposed), then the [operations runbook](docs/OPERATIONS.md). Coding agents: read [docs/CODEBASE_GRAPH.md](docs/CODEBASE_GRAPH.md) first.

## Use TwinLM in your project

TwinLM supports a documented subset of the OpenAI API. Applications using
supported calls can switch `base_url` and `api_key` without a TwinLM client
library; check the [compatibility matrix](docs/ARCHITECTURE.md#current-compatibility-matrix-v1) first.
Unsupported parameters fail with a 400 `unsupported_parameter` error instead
of being silently ignored.

**1. Run the gateway**

```bash
export ADMIN_TOKEN=$(openssl rand -hex 32)   # keep it: needed for /admin
docker run -d --name twinlm -p 8000:8000 -e GEMINI_API_KEY=... -e GROQ_API_KEY=... -e ADMIN_TOKEN -v twinlm-data:/app/data ghcr.io/rsh-119/twinlm:latest
```

Gateway + PostgreSQL without cloning the repo:

```bash
curl -fsSLO https://raw.githubusercontent.com/rsh-119/TwinLM/main/deploy/quickstart.yml
echo "GEMINI_API_KEY=..." >> .env && echo "GROQ_API_KEY=..." >> .env
docker compose -f quickstart.yml up -d
docker compose -f quickstart.yml exec gateway cat /run/secrets/admin_token
```

Or one click on Render (`render.yaml`: image + managed PostgreSQL):
[Deploy to Render](https://render.com/deploy?repo=https://github.com/rsh-119/TwinLM).
Documentation: https://rsh-119.github.io/TwinLM/docs/ (quickstart, config reference, API, operations).
Product site: https://rsh-119.github.io/TwinLM/ (Next.js in `site/`: `cd site && npm install && npm run dev`;
published by `.github/workflows/pages.yml`).

SQLite in the `twinlm-data` volume by default; pass
`-e DATABASE_URL=postgresql+psycopg://user:pass@host/db` for Postgres. To
change providers or aliases, mount your own config:
`-v ./gateway.toml:/app/gateway.toml:ro`. Or run gateway + PostgreSQL with
`docker compose up -d`, adding the console, monitoring or TLS ingress as
profiles (see [Docker](#docker)).

**2. Create an API key** (shown once):

```bash
curl -X POST http://localhost:8000/admin/keys -H "Authorization: Bearer $ADMIN_TOKEN"   -H "Content-Type: application/json" -d '{"name": "my-app", "project": "my-app"}'
```

**3. Point your OpenAI client at it**

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="gw_live_...")
reply = client.chat.completions.create(model="auto", messages=[{"role": "user", "content": "Hi"}])
```

```ts
import OpenAI from "openai";

const client = new OpenAI({ baseURL: "http://localhost:8000/v1", apiKey: "gw_live_..." });
const reply = await client.chat.completions.create({ model: "auto", messages: [{ role: "user", content: "Hi" }] });
```

**Teams and members.** People sign in to the console with email and password; admins, team admins and
members get keys and spend per team (`docs` → Teams and members). First admin: `python -m app.ops create-admin`.

**Add providers without a restart.** In the web console (http://localhost:8000/app) the
Providers tab adds Anthropic, OpenAI, Mistral, DeepSeek and other providers with just an API key (stored
encrypted with `PROVIDER_KEYS_SECRET`), and the Models tab puts them into aliases and sets prices. Changes
apply immediately.

LangChain, LlamaIndex and other OpenAI-compatible frameworks work the same
way: set their base URL and API key. `model` is an alias from
`gateway.toml` (`auto`, `smart`, `fast`), a provider (`gemini`) or
`provider/model`. Adding a provider (OpenAI, Ollama, vLLM, ...) is one
`[providers.x]` block in `gateway.toml`. See [Routing](#routing).

## Contents

[Use TwinLM in your project](#use-twinlm-in-your-project) · [Architecture](#architecture) · [Features](#features) ·
[Supported providers](#supported-providers) · [Configuration](#configuration) ·
[API usage](#api-usage) · [Authentication](#authentication) ·
[Routing](#routing) · [Fallbacks, retries and circuit breakers](#fallbacks-retries-and-circuit-breakers) ·
[Rate limiting](#rate-limiting) · [Budgets and alerts](#budgets-and-alerts) · [Caching](#caching) ·
[Cost tracking](#usage-and-cost-tracking) · [Guardrails and structured output](#guardrails-and-structured-output) ·
[Observability](#observability) · [Docker](#docker) · [Several instances](#running-several-instances) · [Testing](#testing) ·
[Example client](#example-client) · [Web console](#web-console) · [Limitations](#limitations)

## Architecture

```text
Client ─► request-ID middleware ─► auth ─► admission (allowed models, RPM/TPM, budget)
       ─► input guardrails ─► context (memory or as-sent) ─► cache
       ─► router: registry → strategy → circuit breaker → timeout → retry → fallback
       ─► provider adapter ─► LLM
       ─► structured-output validation ─► output guardrails ─► persist / cache
       ─► accounting (request log, metrics, cost) on every path
```

Details, design decisions and known limits are in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Features

- **OpenAI-compatible API**: `/v1/chat/completions` (incl. SSE streaming),
  `/v1/embeddings`, `/v1/models`. Works with the official OpenAI SDK.
- **Cost controls**: output-token cap per alias or globally, exact-match
  cache for deterministic requests, `/admin/savings` against a baseline model.
- **Stateful conversations** (optional): summary, per-user memories and
  recent history, assembled by the gateway.
- **Model registry and routing**: aliases (`auto`, `smart`, `fast`,
  `embed`) map to ordered provider/model targets; priority, cost,
  latency or load strategy.
- **Resilience**: per-attempt timeouts, total deadline, bounded retries
  with jittered backoff, automatic fallback, per-provider circuit breakers.
  Client disconnects cancel the upstream request.
- **Gateway API keys** with per-key allowed models, requests/minute,
  tokens/minute and a daily USD budget.
- **Usage and cost tracking** per request, with config-driven pricing.
- **Guardrails**: secret, PII and prompt-injection detection with
  block/redact/log actions; input size limit.
- **Structured output**: `response_format` `json_object` / `json_schema`,
  validated with bounded repair.
- **Observability**: request IDs, JSON logs with secret redaction,
  Prometheus metrics, provisioned Grafana dashboard, admin API.

## Supported providers

| Provider | Adapter | Status |
|---|---|---|
| Google Gemini | `gemini` | enabled in `gateway.toml` (chat, streaming, embeddings) |
| Groq | `openai_compatible` | enabled (chat, streaming) |
| OpenAI, Ollama, NVIDIA NIM, vLLM, … | `openai_compatible` | add a `[providers.x]` block (examples in `gateway.toml`); untested against the live services |
| Mock | `mock` | no network; for load tests and running without keys |

## Configuration

Two sources, deliberately split:

- **Environment / `.env`**: secrets and deployment switches (see
  [`.env.example`](.env.example)): provider keys, `DATABASE_URL`,
  `AUTH_ENABLED`, `ADMIN_TOKEN`, `WEB_UI_ENABLED`, `LOG_FORMAT`,
  `DEFAULT_MODEL`, `GATEWAY_CONFIG_PATH`.
- **[`gateway.toml`](gateway.toml)**: behavior. Providers (with the *name*
  of the setting holding their key, never the key), model aliases, routing
  strategy, timeouts, retry, circuit breaker, prices, default rate limits,
  cache, guardrails. Validated at startup, so a typo fails fast.

```toml
routing_strategy = "priority"        # priority | latency | cost | load

[providers.groq]
type = "openai_compatible"
base_url = "https://api.groq.com/openai/v1"
default_model = "openai/gpt-oss-20b"
api_key_setting = "groq_api_key"

[models.smart]
targets = ["gemini/gemini-flash-latest", "groq/openai/gpt-oss-20b"]
```

## API usage

```bash
python -m venv .venv && .venv\Scripts\activate   # or: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                              # add GEMINI_API_KEY / GROQ_API_KEY and ADMIN_TOKEN
python -m uvicorn app.main:app --reload
```

Interactive docs: http://127.0.0.1:8000/docs. Without keys, run against
mock providers: `GATEWAY_CONFIG_PATH=loadtest/gateway.mock.toml`.

Auth is on by default: create a key first ([Authentication](#authentication))
and send it as `Authorization: Bearer gw_live_…`. The examples below omit
that header, as with `AUTH_ENABLED=false`.

**Stateless (OpenAI-style).** `messages` go to the provider as sent, and
nothing is stored. `temperature`, `top_p`, `max_tokens`, `stop` and
`response_format` are honored. Tool calling (`tools`, `tool_choice`,
`tool` messages), content-part lists (text and images) and
`stream_options.include_usage` work too, and `finish_reason` is the
provider's real one (`stop`, `length`, `tool_calls`, `content_filter`).
Gemini only takes images as base64 `data:` URLs, and tool calls must be
sent back with their `extra_content` (the OpenAI SDK does this when you
pass the message object back). `n > 1` returns 400.

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer $GATEWAY_API_KEY" -H "Content-Type: application/json" \
  -d '{"model": "smart", "messages": [{"role": "user", "content": "Explain distributed systems"}]}'
```

**Stateful.** Create a conversation, then send only new turns:

```bash
curl -X POST http://localhost:8000/v1/conversations -H "Content-Type: application/json" -d '{"user_id": "u1"}'
curl http://localhost:8000/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"conversation_id": "conv_…", "model": "auto", "messages": [{"role": "user", "content": "I build Kafka systems."}]}'
curl -X POST http://localhost:8000/v1/memories -H "Content-Type: application/json" \
  -d '{"user_id": "u1", "content": "Prefers Python examples."}'
```

Context is the last 10 messages, a summary regenerated every 20 messages,
and the user's memories. Each piece is capped at about 4,000 characters.

**Streaming.** `"stream": true` returns SSE. Each event is an OpenAI
`chat.completion.chunk` that also carries `provider`, `delta` and `done`;
stateless streams end with `data: [DONE]`. Fallback is only possible
before the first token. After that, a provider failure ends the stream
with a short note.

Responses in both modes contain the OpenAI fields (`id`, `object`,
`created`, `choices`, `usage`) plus `provider`, `conversation_id` and
`message`. Every response carries an `X-Request-ID` header.

Also available: `POST /v1/embeddings`, `GET /v1/models`, `GET /health`
(liveness), `GET /ready` (DB reachable), `GET /metrics`, and the
[admin API](#observability).

## Authentication

With `AUTH_ENABLED=true` (the default) every `/v1` route
requires `Authorization: Bearer gw_live_…`, which is exactly what the
OpenAI SDK sends as `api_key`. Keys are created through the admin API.
Only their SHA-256 is stored, and the raw key is returned once.

```bash
curl -X POST http://localhost:8000/admin/keys -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "team-a", "project": "search", "allowed_models": ["smart"], "rpm_limit": 60, "tpm_limit": 100000, "daily_budget_usd": 5}'
curl -X DELETE http://localhost:8000/admin/keys/key_… -H "Authorization: Bearer $ADMIN_TOKEN"   # revoke
```

Missing, invalid and revoked keys get 401; a disallowed model gets 403.
Startup fails if auth is on and `ADMIN_TOKEN` is empty, since no key
could ever be created.

Each key belongs to a **project**. Conversations and memories are scoped
to the project that created them: another project's keys get 404 for
them, and listing memories only returns the caller's project.

A key created with `"user_id": "alice"` is **user-bound**: it only sees
alice's conversations and memories (`user_id` may then be omitted). Keys
without `user_id` are **service keys** for applications and may act for any
user in their project; set `STATEFUL_REQUIRES_USER_KEYS=true` to forbid that
in shared-user deployments. `POST /admin/keys/{id}/rotate` issues a
replacement with the same settings (the old key works until revoked).
`STATEFUL_ENABLED=false` turns off conversations and memories entirely.

`AUTH_ENABLED=false` is for a private single-user setup: no per-key
limits, and everything lives in the `default` project. The browser UI has
no login and is off unless `WEB_UI_ENABLED=true`.

Errors use OpenAI's shape, `{"error": {"message", "type", "param", "code"}}`,
so SDKs show the real reason. The same message is also in `detail`.

## Routing

`model` can be an **alias** from `gateway.toml` (its targets, with
fallback), a **deployment id**, a **provider name** (`gemini`: its default
model, no fallback), or **`provider/model`** (`groq/openai/gpt-oss-20b`:
exactly that, no fallback). `GET /v1/models` lists what the key may use with
each model's capabilities; `GET /admin/models` shows groups, deployments,
capabilities and prices.

A **deployment** is a named provider + model (`[deployments.<id>]`). An alias
can list `groups` of deployments serving the same model (for example two
credentials or regions): the gateway tries the other deployments of a group
before **falling back** to the next group. Each deployment has
**capabilities** (`chat`, `tools`, `vision`, `embeddings`); a request needing
tools or images is only sent to deployments that support them, and gets a
400 naming the missing capability if none do.

`routing_strategy` reorders deployments within a group, and groups by their best member:

| Strategy | Order by |
|---|---|
| `priority` | config order |
| `cost` | input + output price per million tokens (unpriced targets last) |
| `latency` | moving average of recent successful latency (unmeasured first, so they get measured) |
| `load` | requests currently in flight to the provider |

Providers whose circuit is open are skipped by every strategy.
"Capability routing" is expressed through aliases (`smart`, `fast`,
`embed`); the gateway does not classify prompts.

## Fallbacks, retries and circuit breakers

| Failure | Behavior |
|---|---|
| 400 / 401 / 403 / other client errors | returned immediately; never retried, never falls back |
| 429 (and Groq's 413 tokens-per-minute) | no same-provider retry; next target |
| 5xx, timeout, connection error, malformed response | retry same provider (`retry.max_retries`, exponential backoff with full jitter), then next target |

Each attempt is bounded by the provider's `timeout_seconds`, and the whole
request by `request_timeout_seconds`. For streams the deadline bounds the
time to first token; after that each chunk must arrive within the
provider timeout.

**Circuit breaker** (per provider): `failure_threshold` consecutive
availability failures open it, and the provider is skipped for
`cooldown_seconds`. Then one trial request is let through (half-open):
success closes the circuit, failure reopens it. State is visible at
`GET /admin/providers` and in the `llm_provider_health` metric.

## Rate limiting

Per API key, sliding 60-second windows: **requests/minute** and
**tokens/minute** (tokens are counted when the provider reports them, so
one request can overshoot before the next is rejected). Exceeding a
window returns 429 with `Retry-After`. Keys without their own limits use
`[rate_limits]` defaults from `gateway.toml`.

Limits are kept in-process, so they apply per gateway instance, unless
`REDIS_URL` is set ([several instances](#running-several-instances)).

## Budgets and alerts

Spending limits in USD, checked before every request:

| Budget | Set with | Period |
|---|---|---|
| Per API key | `daily_budget_usd` on the key | day (UTC) |
| Per team (project) | `PUT /admin/budgets {"scope": "team", "target": "search", "limit_usd": 500}` | `daily` or `monthly` |
| Per person | `{"scope": "user", "target": "<user id>", ...}`: their personal keys and playground use | `daily` or `monthly` |
| Whole company | `{"scope": "org", "limit_usd": 2000}` | `daily` or `monthly` |

Or in the console: **Budgets** (admins). A request is refused with 429
(`team 'search' monthly budget exceeded`) when settled spend, spend still
being written, requests in flight and **this request's worst case** would
cross any budget that applies. The worst case is the input (≈ characters/3
tokens) plus the output cap (`max_tokens` or `default_max_output_tokens`), at
the priciest model the alias may fall back to. It is reserved while the
request runs and replaced by the real cost when it ends, so concurrent
requests can't overshoot together. Without an output cap only the input is
reserved; unpriced models reserve nothing and stop only the next request.

**Alerts** fire once per budget period at 80% and at 100% (also on the first
refused request): a log line, the `llm_budget_alerts_total` metric, the
console's alert list (`GET /admin/budget-alerts`) and, with
`BUDGET_ALERT_WEBHOOK_URL`, a JSON POST with a Slack-style `text` field.
`GET /admin/budgets` shows each budget's spend this period; team members see
their team's and their own budget on the team page.

## Caching

Exact-match cache for **stateless, non-streaming, deterministic** requests
(`temperature` unset or 0), on in the shipped `gateway.toml`
(`[cache] enabled`). Sampled requests are never cached because their replies
are meant to vary. Per alias, `cache_ttl_seconds` overrides the TTL and `0`
disables caching. The key is a SHA-256 of model, messages,
sampling params, response format and the caller's project, so answers
never cross projects. Requests where the guardrails detect secrets or PII
are never cached. Generated sensitive output and tool-call turns are also
excluded. Responses carry `X-Cache: HIT|MISS`, hits are logged at zero cost,
and `DELETE /admin/cache` clears it. LRU with TTL and a byte cap, in-process.
**Semantic cache** (opt-in per alias). An FAQ or support bot gets the same
question in many wordings. With

```toml
[cache.semantic]
models = ["faq"]           # aliases that may use it
embedding_model = "embed"  # an alias with embeddings
min_similarity = 0.95
```

a request to `faq` that misses the exact cache embeds its last user message
and reuses a cached answer whose question has cosine similarity ≥ 0.95, with
everything before that message (system prompt, history, params, project)
identical. Hits carry `X-Cache: HIT` and `X-Cache-Similarity`. Each miss costs
one embeddings call, logged as its own request. Similar is not identical
("capital of France?" vs "capital of Germany?" score about 0.9), so keep it to
aliases where a near-match answer is acceptable. In-process, per instance.

## Usage and cost tracking

Every request (successes, errors, blocks, cache hits, client
disconnects) produces one `request_logs` row: request ID, key, project,
provider, model, tokens, cost, latency, time to first token, status,
fallback used, cache hit. Prompts and replies are never stored there.
Prices are USD per million tokens in `[pricing."provider/model"]`. The
shipped values are examples to verify. An unpriced model is recorded
with `cost = null`, not 0.

```bash
curl "http://localhost:8000/admin/usage?hours=24&group_by=model" -H "Authorization: Bearer $ADMIN_TOKEN"
```

`group_by` is `provider`, `model`, `deployment`, `project` or `api_key_id`.
The response includes requests, errors, fallbacks, cache hits, provider tokens
(cache hits excluded), known cost, `unpriced_requests` and `cost_complete`,
average latency and TTFT, and p50/p95 per provider. Missing usage counts from
a provider are recorded as unknown, never as zero.

**Savings report.** Set `[savings] baseline = "provider/model"` (with a
`[pricing]` entry): the model you would otherwise send everything to.

```bash
curl "http://localhost:8000/admin/savings?hours=720&group_by=project" -H "Authorization: Bearer $ADMIN_TOKEN"
```

It returns, per group and in total, `actual_cost_usd`, `baseline_cost_usd`
(the same tokens at baseline prices), `savings_usd`/`savings_pct`, and
`cache_avoided_usd`. These are estimates: token counts come from the models
that served the requests, and requests with unknown tokens or price are left
out (`measured_requests` < `requests`, `complete: false`).

**Output caps.** `default_max_output_tokens` (and per-alias
`max_output_tokens`) fill in `max_tokens` when a client omits it and clamp it
when a client asks for more. A clamped reply ends with `finish_reason:
"length"`.

## Guardrails and structured output

Configured per stage in `[guardrails.input]` / `[guardrails.output]`:

| Guardrail | Detects | Actions |
|---|---|---|
| `secrets` | private keys, AWS/Google/GitHub/Slack tokens, `sk-`/`gsk-`/`nvapi-` keys, gateway keys, JWTs | block, redact, log |
| `pii` | emails, Luhn-valid card numbers, US SSNs, phone numbers | block, redact, log |
| `prompt_injection` | common "ignore previous instructions" / "reveal the system prompt" / role-override phrasing in user messages | block, log |

Plus `max_input_chars`. A blocked input returns 400 and never reaches the
provider. A blocked output returns 502. Redactions also apply to what is
stored for stateful conversations. Detection is regex-based: it catches
common leaks, not adversarial inputs. A streamed reply can only be
checked after it is sent, so findings there are logged, not blocked.

**Structured output.** `response_format: {"type": "json_object"}` or
`{"type": "json_schema", "json_schema": {"schema": {...}}}`. The gateway
instructs the model, parses the reply (stripping code fences), and
validates it with JSON Schema. On failure it asks the model to repair up
to `max_repair_attempts` times; if the reply is still invalid it returns
502. Not supported with `stream=true`.

## Observability

- **Logs**: JSON lines (`LOG_FORMAT=json`) with `request_id`. Bearer
  tokens, gateway keys, provider keys and `?key=` query params are
  redacted from every line. Prompts are not logged.
- **Metrics** at `/metrics`: `llm_requests_total`,
  `llm_request_latency_seconds`, `llm_provider_latency_seconds`,
  `llm_time_to_first_token_seconds`, `llm_request_errors_total`,
  `llm_tokens_total`, `llm_cost_usd_total`, `llm_fallback_total`,
  `llm_retries_total`, `llm_cache_hits_total`, `llm_cache_misses_total`,
  `llm_provider_health`, `llm_active_requests`,
  `llm_guardrail_violations_total`,
  `llm_structured_output_failures_total`, `llm_rate_limited_total`,
  `llm_budget_alerts_total`.
- **Grafana**: the "TwinLM Gateway" dashboard is provisioned by compose.
- **Admin API** (`Authorization: Bearer $ADMIN_TOKEN`; disabled when
  unset): `/admin/providers`, `/admin/models`, `/admin/usage`,
  `/admin/keys`, `/admin/cache`, `/admin/budgets`, `/admin/budget-alerts`.
- No distributed tracing (OpenTelemetry); request IDs correlate logs and
  usage rows.

## Docker

The default is the minimal deployment (secrets, PostgreSQL, gateway);
everything else is an opt-in profile:

```bash
docker compose up -d --build                                  # gateway + PostgreSQL
docker compose --profile monitoring up -d                     # + Prometheus, Grafana
docker compose --profile ingress up -d                        # + Caddy TLS ingress (docs/OPERATIONS.md)
```

| Open | What |
|---|---|
| http://localhost:8000/docs | Gateway API |
| http://localhost:8000/app | **Web console**: sign in, teams, keys, providers, models, usage, savings |
| http://localhost:3001 | Grafana, "TwinLM Gateway" dashboard (`monitoring` profile, read-only without login) |
| http://localhost:9090 | Prometheus with alert rules (`monitoring` profile) |

Nothing to configure first:

- **Secrets are generated on first start.** A one-shot `secrets`
  container writes random values for the admin token and the Postgres and
  Grafana passwords into the `secrets` volume, and they are reused on
  every restart. No password lives in a file. To print one:
  `docker compose exec gateway cat /run/secrets/admin_token` (likewise
  `grafana_password` for the Grafana admin login).
- **Provider keys** come from `.env` if present. With no keys, add
  `GATEWAY_CONFIG_FILE=./loadtest/gateway.mock.toml` to `.env` for mock
  providers.
- **Ports are bound to 127.0.0.1 only.** For shared access use the
  `ingress` profile (TLS; only `/v1` public on the API domain; the console domain has its own sign-in)
  rather than widening `*_BIND`.

Services: **secrets** (one-shot), **gateway** (non-root, health check on
`/ready`, auth on, `gateway.toml` mounted read-only, migrations applied on
start), **postgres** (volume, health check); profiles: **ui**,
**prometheus** + **grafana**, **caddy**. Images are pinned by digest and
Python dependencies are hash-locked (`requirements.lock`). Backups,
restores, retention, key rotation and outbox reconciliation:
[docs/OPERATIONS.md](docs/OPERATIONS.md). No Redis or Qdrant: nothing uses them.

```bash
docker compose logs -f gateway   # JSON logs
docker compose down              # stop (data kept)
docker compose down -v           # stop and delete data + generated secrets
```

The gateway image alone: `docker build -t twinlm . && docker run -p 8000:8000 --env-file .env twinlm`
(SQLite at `/app/data`; mount a volume there to keep it).

## Running several instances

One instance is the default. To run several behind a load balancer, point
them at the same PostgreSQL and set `REDIS_URL` (Redis 6.2+). Then these are
shared: rate-limit windows, the exact-match cache, budget reservations, API-key
revocation (immediate on every instance) and providers/aliases/prices changed
in the console (other instances reload within 5 seconds). Still per instance:
circuit breakers and latency stats (each learns provider health from its own
traffic), the semantic cache and the usage outbox. If Redis is unreachable,
limits and the cache fail open (requests are served, unlimited and uncached)
and the gateway logs it; Redis is skipped for 10 s after an error so an outage
doesn't slow requests down.

To try it on one machine, `loadtest/compose.scale.yml` adds Redis, a second gateway and a Caddy load
balancer to the Compose stack: `docker compose -f docker-compose.yml -f loadtest/compose.scale.yml up -d`.
`python loadtest/bench.py --instances 2` benchmarks that setup.

## Testing

```bash
pip install -r requirements-dev.txt  # or the hash-locked requirements-dev.lock (Linux/Python 3.11)
python -m pytest                     # offline suite incl. OpenAI SDK contract tests
TEST_DATABASE_URL=postgresql+psycopg://... python -m pytest   # + PostgreSQL migrations/integration (DISPOSABLE database only)
REDIS_TEST_URL=redis://localhost:6379/0 python -m pytest   # shared-state tests on a real Redis (DISPOSABLE: flushed)
python scripts/codebase_graph.py     # codebase graph is current
python loadtest/bench.py             # whole-stack benchmark in a throwaway Compose project
python scripts/demo.py               # end-to-end demo against real providers (needs keys)
```

Coverage includes OpenAI compatibility, routing, retries, timeouts, the
deadline, the circuit breaker, malformed provider output, stream
cancellation, auth, limits, budgets, accounting, cache, guardrails,
structured output, metrics, log redaction, and a regression test for the
DB-pool deadlock found under load.

## Example client

[`examples/client.py`](examples/client.py) uses the official OpenAI SDK:
model listing, a completion, streaming, structured output, and stateful
mode through `extra_body={"conversation_id": ...}`.

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="gw_live_…")
response = client.chat.completions.create(
    model="smart",
    messages=[{"role": "user", "content": "Explain distributed systems"}],
)
print(response.choices[0].message.content)
```

## Web console

The gateway serves a web console at **http://localhost:8000/app** (Next.js, built into the gateway image; source
in `console/`). People sign in with their own email and password; on first start it asks for the admin token
once to create the first admin.

| Page | What |
|---|---|
| Overview | Requests, spend, savings and cache hit rate for 30 days; spend per day; top models |
| Playground | Chat with any model, streaming or not; billed to your team; shows provider, latency, tokens and cache hits |
| Teams | Members with roles and last activity, team keys, team usage |
| People | Everyone who can sign in: add, make admin, disable, reset password (admins) |
| Keys | All active keys across teams (admins) |
| Providers | Add providers with an API key, test the connection, enable/disable, replace keys (admins) |
| Models | What each model name tries in order, output caps, cache TTL, prices (admins) |
| Usage, Savings, Health | Company-wide reports and provider status (admins) |
| Budgets | Company, team and person spending limits with live progress and alerts (admins) |

Develop it with the gateway running on :8000:

```bash
cd console && npm install && npm run dev      # http://localhost:3000/app (API proxied to :8000)
npx playwright test                           # end-to-end test against a running gateway (TWINLM_URL)
```

## Limitations

- Without `REDIS_URL`, breakers, rate-limit windows, budget reservations and
  the cache are per process: one gateway instance. With it, see
  [several instances](#running-several-instances).
- Measured capacity and overhead, one and two instances: [`docs/PERFORMANCE.md`](docs/PERFORMANCE.md)
  (one machine, mock providers; not a capacity guarantee).
- Budgets are hard only for priced models with an output cap (see
  [Budgets](#budgets-and-alerts)); no SSO (user identity = which key a person holds).
- `n > 1`, batch/queued execution and tracing are not implemented.
- Tools and images are stateless-only: with `conversation_id` they return
  400. Falling back from one provider to another mid tool-conversation
  works, but tool-call ids don't carry over.
- Gemini embeddings report no token usage: responses show 0 tokens and the
  request log records null tokens and cost.

## License

[MIT](LICENSE)
