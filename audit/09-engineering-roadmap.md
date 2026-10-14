# Prioritized engineering roadmap

This is a proposal; no implementation was performed. Effort ranges assume one experienced engineer familiar with this repository, excluding provider/hosting approvals and review delays. Priorities distinguish launch correctness from optional growth. Validate acceptance criteria before promotion; passing a generic suite alone is insufficient.

## P0 — critical blockers

| Task / files | Why / benefit | Dependencies | Effort | Acceptance criteria and verification |
|---|---|---|---|---|
| R01: offline schema registry and bounded refs — `gateway/structured.py`, API schema path (F01) | Remove authenticated SSRF/blocking loader | None | 1–2 days | Local refs still validate; external ref/dynamicRef never calls filesystem/network loader; bounded malformed schema errors via offline tests |
| R02: unique execution IDs and recoverable usage writes — `observability.py`, `database/models.py`, `gateway/usage.py` (F02/F04) | Prevent caller IDs dropping unrelated ledger rows | Migration strategy; R01 independent | 3–5 days for identifiers/retry, plus durable ledger below | Duplicate X-Request-ID across keys/batches preserves all independent events; transient fault retry visible; poison row isolated; no silent drop in background-writer integration tests |
| R03: complete outbound data policy — `api/chat.py`, `api/memories.py`, `memory/context_builder.py`, `gateway/guardrails.py` (F03) | Stop facts bypassing secret blocking; bound context | Owner data/stream policy | 2–4 days | Synthetic secrets in facts/history/schema/tool arguments handled consistently before fake provider; no unintended role promotion; prompt bounds tested |
| R04: clarify/enforce spend contract — `api/common.py`, `gateway/usage.py`, `pricing.py`, `rate_limit.py`, DB ledger (F05/F14) | Bound in-flight spend and account internal calls | R02, output cap, verified price mapping; owner hard/advisory decision | 1–2 weeks for hard ledger/reservations | N simultaneous requests cannot exceed chosen reserved exposure; crash releases/reconciles; unknown price/usage policy explicit; summary/repair attributed; compare synthetic invoices |
| R05: atomic/versioned stateful turns — `api/chat.py`, `conversation_manager.py`, `repository.py`, DB models (F06) | Eliminate half exchanges and stale updates | Turn conflict/idempotency contract | 3–5 days | Fault on assistant write leaves neither new message; two turns deterministically serialize/reject conflict; delete/summary race safe; repeat idempotency key does not duplicate |
| R06: protect operator surfaces before exposure — `ui/streamlit_app.py`, `web/routes.py`, Compose/ingress (F10) | Prevent console admin inheritance/credential forwarding and auth bypass | Deployment trust model | 1–3 days for private perimeter/origin allowlist; shared UI login may take longer | Public unauthenticated sessions cannot operate console or bypass gateway auth; outbound target fixed/allowlisted; private operational smoke |

R01, R02, R03, R04 and R05 are the first five tasks. R06 moves ahead of any work if the console/web UI is already reachable by untrusted users. No evidence of current public exposure was collected.

## P1 — production essentials

| Task / files | Why / benefit | Dependencies | Effort | Acceptance criteria and verification |
|---|---|---|---|---|
| R07: terminal stream outcome and final schema guarantees — `model_router.py`, `api/chat.py`, `structured.py` (F07/F08) | Correct incomplete replies, SLI and response contracts | R03/R05 policy, client compatibility | 3–5 days | Drop after text/tool event yields explicit incomplete outcome, not success; actual returned/cached redacted JSON conforms or errors; socket tests |
| R08: eager startup and typed upstream errors — `main.py`, `api/common.py`, adapters (F09/F11) | Fail deployment early; actionable errors | Supported adapter policy | 2–3 days | Missing/invalid TOML fails startup; readiness only after initialized services; 400/401/403/429/5xx mapping matrix without sensitive values |
| R09: absolute deadline/backpressure/input-output bounds — `api/chat.py`, router, schemas, ingress (F15/F16) | Bound resource use and latency | Agreed stream/workload limits, R04 | 3–5 days | Repair+summary respects one deadline; endless/slow stream cancels under cap; schema/image/tool/fact body and active requests bounded; no leaked clients |
| R10: migrations and DB correctness — `database/*`, release job (F18) | Safe old DB upgrade and deploy compatibility | Inventory existing versions without reading sensitive row contents | 3–5 days | Versioned upgrade from old schemas, serialized startup, index/backfill checks, SQLite FK/Postgres UTC/constraints tested |
| R11: backup, retention, restore/runbook — deployment/docs, DB maintenance (F20) | Recover tenant history/keys/ledger and limit growth | Hosting/data RPO/RTO/retention decisions, R04/R10 | 3–5 days plus drill time | Encrypted off-host backup restored in isolated environment within agreed RTO/RPO; deletion/retention policy tested; no secrets in logs |
| R12: production-path CI and dependency assurance — requirements/UI lock, `.github/workflows`, Dockerfiles (F19) | Repeatable runtime and dialect/provider protocol gates | R10, build environment | 3–5 days | Locked install on Python 3.11; PostgreSQL+background writer tests; PR image startup/non-root/health; reviewed dependency/image scan; immutable release artifact |
| R13: accurate telemetry and actionable alerts — `observability.py`, writer, admin, monitoring (F17/F20) | Detect lost usage, incomplete streams and saturation | R02/R04/R07 and agreed SLO | 2–4 days | Writer queue age/loss, DB waits, loop lag, bounded labels; dashboard query checks; staged alert delivery and runbook exercise |
| R14: memory/summarization correctness — `memory/summarizer.py`, context, chat (F13/F14) | Preserve facts, avoid hidden cost/policy bypass | Turn version/R04/R03 | 2–4 days | Prior facts persist over multiple >24k transcript summaries; no unauthorized model; internal usage settled; stale summary version rejected |
| R15: current support/operations docs — README/docs/examples | Remove stale claims and undocumented startup/restore steps | Verified fixes/release | 1–2 days | New engineer follows private deployment, key rotation, backup, rollback; accurate API/model support and test evidence |

## P2 — workload-driven scalability/performance

| Task / files | Why / benefit | Dependencies | Effort | Acceptance criteria and verification |
|---|---|---|---|---|
| R16: bound history/API queries, explain/index — repository/API/admin/web (F12) | Avoid growth-related reads and misleading model percentiles | Representative synthetic data, R10 | 2–4 days | Last-10 query reads bounded rows; paginated lists deterministic; per-model stats correct; EXPLAIN and history sweep artifacts |
| R17: profile and address synchronous DB — `database.py`, async handlers | Reduce measured event-loop stalls | R16 and production-like baseline | 3–7 days | Demonstrated lower loop/pool wait at target load with unchanged correctness; whole-session thread offload or async engine chosen from evidence |
| R18: shared atomic quotas/reservations and replica validation — limiter/ledger/deployment | Preserve global policy with multiple workers | Actual scaling need, R04/R13 | 1–2 weeks | Aggregate N-worker RPM/TPM/spend stays within contract; store outage policy works; fairness/load test and operational cost acceptable |
| R19: cache bytes/version/single-flight as justified — `gateway/cache.py`, chat | Reduce useful eligible inference while bounding sensitive state | Observed cache demand/R03 | 2–4 days | Config/model revision invalidates stale entries; byte cap honored; generated sensitive tools excluded; measured hit/latency benefit |
| R20: reproducible representative load/soak — `loadtest`, test deployment | Establish actual capacity and bottleneck order | R09/R13/R16, SLO and safe infra | 3–5 days | Raw artifacts for all required metrics and mixes; overload recovery/zero unreported loss; no paid production traffic |

Only undertake R17–R19 where profiling, global policy, or agreed workload justifies cost. Redis is a reasonable option for shared short-lived windows; PostgreSQL reservations may suffice. Local breakers and cache can remain local without compromising global spend contracts.

## P3 — advanced/optional

| Task / modules | Benefit / trade-off | Dependencies | Effort | Acceptance / verification |
|---|---|---|---|---|
| OTel provider/DB spans | Faster complex latency debugging, extra operational data/deps | Accurate metrics, demonstrated debugging need | 2–4 days | Correlated bounded/redacted spans explain a staged failure |
| Durable summary worker via DB jobs | Reduce inline turn latency; adds job/replay coordination | R05/R10/R14; observed summary latency | 4–7 days | Unique versioned jobs, crash/retry safety, recorded cost, stale-job rejection |
| New native adapter/advanced tools | Add requested provider capability; larger support surface | User demand and conformance fixtures | 3–10 days each | Supported live and offline contract, failure/tool/usage matrix |
| Semantic cache/RAG | Potential domain-specific reuse/retrieval; false positives and embedding costs | Explicit product requirement and evaluation dataset | 1–3 weeks | Quality/permission/cost improvement demonstrated before rollout |
| More HA infrastructure | Meet higher agreed availability/RTO; operating cost | Target cannot be met by simpler host/managed DB setup | Scope-dependent | Failure/recovery and rollout drill meets agreed objective |

There is no proposed Kafka, Kubernetes, service mesh, or microservice rewrite. Revisit topology after measured evidence and operational requirements, not as a prerequisite to basic correctness.
