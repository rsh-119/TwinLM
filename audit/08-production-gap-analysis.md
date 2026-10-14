# Production readiness checklist

Status vocabulary: **Ready** = production-ready for a narrowly stated capability; **Harden** = implemented but needs hardening; **Partial** = meaningful part missing; **Missing** = absent; **N/A** = not applicable; **Unverified** = unable to verify operational claim. No complete subsystem is classified Ready for unrestricted production. Complexity: S ≈ up to 2 engineering days, M ≈ 3–5 days, L ≈ 1–2 weeks; external approvals/infrastructure not included. Priority is conditional on chosen launch contract.

Each row records the gap, its consequence, solution, dependency, and acceptance evidence. IDs refer to detailed findings in reports 03/04; file references there are part of this checklist's evidence.

| Area/capability | Status | Gap and why it matters / consequence if ignored | Solution / expected benefit | Complexity | Dependencies | Priority | Acceptance / verification |
|---|---|---|---|---|---|---|---|
| Modular architecture | Harden | API owns transaction and lifecycle details; changes can diverge paths | Explicit orchestration boundary, shared outcome/deadline types | M | F06/F07 contract | P1 | JSON/SSE/stateful regressions |
| Provider interface | Harden | Protocol capabilities/typed errors incomplete | Capability map and consistent errors | M | Supported providers | P1 | MockTransport conformance matrix |
| API schemas/version | Harden | Silent unknown fields, tool dicts, n<=0, missing compatibility policy | Validate supported shapes and document exclusions | M | API support decision | P1 | SDK contract and invalid-input tests |
| Upstream error contract | Partial | F11 400→502 masks caller/operator errors | Typed sanitized errors | S | Provider error policy | P1 | HTTP status matrix |
| Stream terminal outcome | Partial | F07 incomplete stream appears success | Explicit interrupted status/error events | M | Client compatibility policy | P1 | Socket drop + logs/metrics/history assertions |
| Request deadline | Partial | F15 resets across repair/summary, only idle after stream start | Shared absolute deadline and stream duration cap | M | Latency contract | P1 | Cumulative timeout/cancellation tests |
| End-to-end idempotency | Missing | Client retries can duplicate inference/history | Auth-scoped idempotency key for stateful turn where required | M | F02/F06 | P1 | Replay/concurrent same-key test |
| Project auth | Harden | Tested project boundary but arbitrary user_id within project | Document boundary or implement user claims/ownership | M/L | Trust model | P0 decision | Cross-user/project access matrix |
| Schema reference safety | Partial | F01 authenticated remote loader | Offline registry and reference limits | S | None | P0 | No outbound loader under hostile refs |
| Outbound data policy | Partial | F03 memory, history, schema/tools bypass portions | Check full payload with role/trust policy | M | Data sensitivity/stream policy | P0/P1 | Memory/tool/schema/redaction regressions |
| Final structured response | Partial | F08 redaction invalidates schema | Final validation with explicit policy conflict | S/M | Data policy | P1 | Validate actual returned/cached JSON |
| Console/admin boundary | Partial | F10 session inherits admin; arbitrary URL forwarding | Private ingress, allowlist URL, role/session policy if shared | S/M | Operator access model | P0 if exposed | Unauthenticated access and destination denial |
| TLS and internal endpoints | Missing | No configured encrypted public ingress; metrics/admin exposed if gateway bound public | TLS proxy plus route/network restrictions | M | Hosting/domain | P1 before exposure | External route/TLS smoke in staging |
| Secret management | Harden | Common readable volume, no rotation policy | Separate service secrets and rotation procedure | M | Hosting secret mechanism | P1 | Least-privilege mount/rotation drill |
| Dependency assurance | Missing | F19 unlocked builds, no scanning | Lock/hash runtime/dev/UI and scan images | M | Tested runtime | P1 | Reproducible install + reviewed scan |
| Usage identifiers | Partial | F02 duplicated client header corrupts ledger | Unique server execution ID separate correlation | S | None | P0 | Duplicate header never drops rows |
| Reliable accounting | Partial | F04 queue/batch failures lose spend | Durable ledger/outbox, retry, bad-row quarantine, reconciliation | L | Unique IDs, spend contract | P0 | Crash/DB outage replay with no unreported loss |
| Hard budgets/TPM | Partial | F05 completed-only, null-cost and in-flight overshoot | Reserve/settle atomically or declare advisory and add upstream caps | L | Pricing/output limits, budget decision | P0 if hard | Concurrent reservations enforce chosen bound |
| Atomic exchange | Partial | F06 separate commits and stale context | Transaction for both messages, optimistic sequence/version | M | Conversation conflict contract | P0 stateful | Inject second-write fault; concurrent-turn test |
| Schema migration history | Partial | F18 additive race, no version/backfill history | Alembic baseline and serialized release step | M | Existing-schema inventory | P1 | Upgrade/rollback compatibility fixtures |
| DB integrity/time/money | Harden | SQLite FK off, ambiguous timestamps/float/48-bit row IDs | Enable/test FK; UTC convention; ledger units; longer IDs | M | Dialect and ledger choice | P1/P2 | PostgreSQL constraints/midnight/collision handling |
| Query bounds/pagination | Partial | F12 all-history/fact/key scans and admin sample queries | SQL LIMIT/keyset pagination, composite indexes after EXPLAIN | M | Workload fixtures | P1/P2 | Plans and history-size memory/latency sweep |
| Retention/deletion | Missing | Facts/logs grow; memory deletion API absent | Retention jobs and deletion semantics incl backups/cache | M | Data policy | P1 | Synthetic tenant deletion and retention drill |
| Backup/recovery | Missing | F20 volumes are only local persistence | Encrypted off-host/managed backups; restore drill | M | RPO/RTO, hosting | P1 launch gate | Restore keys/history/ledger to isolated DB |
| DB I/O/pool configuration | Harden | F12 synchronous loop, default waits | Bound timeouts/pool; async or offloaded session units | M/L | Baseline profile | P1/P2 | Event-loop/pool saturation regression |
| Request/output backpressure | Missing | F16 no concurrency/byte/duration cap | Admission semaphores, body/output/context limits | M | Workload/latency targets | P1 | Bounded overload memory and clear rejection |
| Multiworker limits | Missing | Local state multiplies policy | Atomic shared windows/reservations, explicit fail policy | L | Replicas actually needed | P2 before scale | N-worker global-limit and store-outage tests |
| Cache | Harden | Entry not byte bounds; no config/model version; generated tool data unchecked | Byte cap, version namespace, output eligibility policy | M | Cache demand | P1/P2 | Sensitive output and invalidation tests |
| Benchmarks/capacity | Unverified current | Historical mock ceiling cannot size production | Representative isolated harness/raw artifacts | M | Safe test deployment | P2 | RPS/p50/p95/p99/error/CPU/RAM/DB/queue report |
| Logging/redaction | Harden | Value-bearing schema/error data and timezone/retention gaps | Safe structured diagnostics, UTC timestamp, retention | S/M | Data policy/log sink | P1 | Synthetic secret log/DB-error test |
| Metrics/dashboard | Harden/Unverified runtime | F07/F14 false success/cost; no writer/pool/loop panels | Correct outcomes and minimum metrics/panels | M | Accounting/stream fixes | P1 | Scrape and alert simulations |
| Alerts/SLO/incident ownership | Missing | Undetected loss/outage; undefined objectives | Agree SLOs; actionable alerts and runbooks | M | Owner requirements | P1 | Alert routing drill and incident walkthrough |
| Tracing | Missing | No internal/external spans; current IDs offer basic correlation | Add OTel only when debugging demands | M | Accurate metrics and need | P3 | Trace correlation/redaction checks |
| Unit/API/router tests | Harden | 198 pass but gaps proven by probes | Add meaningful issue regressions | M | F01–F09 | P1 | Offline suite and production-path tests |
| PostgreSQL/container/SDK gate | Missing | Test/runtime dialect and network differences untested now | Disposable PG + writer, container/socket smoke | M | CI services/dependency locks | P1 | CI evidence with immutable image |
| CI/release/rollback | Partial | Test/build/push workflow only; current run unknown | Scan/build smoke, migrations, immutable deploy/drain/rollback | M/L | Hosting, schema compatibility | P1 | Staging release and rollback drill |
| Image/resources | Harden | No image size/runtime scan/limits measured | Size inventory, optional dev deps split, resource/log caps | M | Container build permission | P1/P2 | Image non-root/health/restart/resource checks |
| Documentation | Partial | Stale counts/defaults/finished feature statements | One current support/operating matrix, historical reports labeled | S | Verified release | P1 | Another engineer follows startup/recovery |
| Model versioning/evaluation | Partial | Mutable latest alias; no quality dataset | Pin supported versions; small fallback/tool/memory eval | M | Product quality requirements | P1/P2 | Contract and retention fixtures per version |
| Summary durability/quality | Partial | F13 prior facts lost, unmetered inline call | Incremental versioned summary + explicit cost policy | M | Turn version/ledger | P1 | Long history fact-retention/cost test |
| Summary job infrastructure | Missing | Optional inline latency removal absent | DB job table/worker only if latency target needs it | M/L | Durable jobs/idempotency | P2/P3 | Restart/replay/version conflict test |
| RAG/vector DB/semantic cache | N/A/Missing optional | No retrieval requirement | Keep absent unless measured value | L if added | Product demand/eval corpus | P3 | Quality/cost false-positive evaluation |
| Autonomous tools/agent loop | N/A | Gateway returns calls, executes no tools | Keep execution controls in future executor | Depends | Explicit feature scope | P3 | No present requirement |

A private pilot may use local breakers/cache, one worker, a small host and no tracing/broker. A hard-budget, sensitive-data, multi-user public product needs stronger ledger, authorization, TLS, retention and recovery guarantees. The conditional rows should not be treated as approved implementations.
