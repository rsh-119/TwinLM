# TwinLM production readiness audit

> Historical pre-remediation snapshot. Use [the maintained architecture](../docs/ARCHITECTURE.md)
> for current behavior and planned work; the score below is not a current score.

Audit date: 2026-10-14 (Asia/Calcutta). Scope: the current working tree, including staged, unstaged, and untracked project files. Baseline HEAD: `a2a04efcc2a5390130c7a33ec2e1f0e9d1c7e7fa`. This is an engineering assessment, not a certification.

## Verdict

**51/100: substantial functional prototype, not ready for an unrestricted production launch.** A controlled private evaluation is reasonable after fixing the P0 findings and establishing recovery procedures. Passing offline tests does not establish live-provider compatibility, financial accuracy, or operational readiness.

TwinLM is a self-hosted gateway for application developers who want a common API for multiple LLM providers. Applications authenticate with a project API key, request a model alias, and receive a completion or embedding. The gateway selects a provider, retries availability failures, optionally falls back, and tracks usage. Optional conversations store text, user facts, and periodic summaries so providers can share context. This is a modular monolith with optional operator interfaces, not a RAG system or autonomous tool executor.

Verified strengths: 198 passing offline tests; project isolation checks; high-entropy hashed API keys and revocation; pooled HTTP clients; bounded retry with jitter; explicit generator cleanup; non-root gateway container; useful Prometheus instrumentation; a compact architecture with no unnecessary broker or vector database.

## Explicit scoring rubric

Each category receives 0–10: 0 absent/broken, 2 rudimentary, 4 implemented with material launch gaps, 6 credible functional implementation with limited operational proof, 8 demonstrated hardening, 10 demonstrated operation against agreed targets. Ten equally weighted categories sum to 100. Unknown deployment requirements and missing verification reduce confidence, not merely the numerical score. Mandatory security/data-integrity gates override any total.

| Category | Score | Evidence and principal deduction |
|---|---:|---|
| Architecture | 7 | Clear API/router/provider modules; transaction ownership and lifecycle remain coupled (`app/api/chat.py:84`, `app/database/repository.py:58`) |
| API engineering | 6 | Schemas and SSE exist; upstream 400 becomes 502; stream failure appears successful (`app/api/common.py:102`, `app/router/model_router.py:465`) |
| Data management | 4 | ORM schema and tenant scoping exist; separate exchange commits, ad hoc DDL, no recovery procedure (`app/database/database.py:23`) |
| Security | 4 | Secure auth defaults; schema reference retrieval and memory guardrail bypass confirmed (`app/gateway/structured.py:25`) |
| Performance | 5 | Historical mock benchmark documented; synchronous DB and unbounded history loading remain (`app/database/repository.py:99`) |
| Scalability | 4 | Async upstream I/O; local admission state and no admission concurrency cap (`app/gateway/rate_limit.py:21`) |
| Reliability | 4 | Retry/breaker/deadlines; usage batches discarded on write failure (`app/gateway/usage.py:203`) |
| Observability | 6 | 16 application metric families and dashboard; inaccurate success/cost paths, no writer alerts (`app/observability.py:24`) |
| Testing | 7 | 198 pass, meaningful transport/failure regressions; no current PostgreSQL/container/provider proof (`tests/conftest.py:140`) |
| Deployment/operations | 4 | Compose and CI definitions; unpinned dependencies, no restore or release drill (`.github/workflows/ci.yml:18`) |
| **Total** | **51** | **No certification or capacity guarantee** |

## Top ten critical issues

1. **F01 — High/P0:** untrusted JSON Schema references invoke a remote loader; SSRF and event-loop blocking risk. Verified interception, no actual network call.
2. **F02 — High/P0:** caller-supplied request IDs are usage primary keys; a duplicate can roll back unrelated rows in a batch. Verified three submitted, zero stored.
3. **F03 — High/P0:** stored memories bypass secret blocking and enter system context. Verified with synthetic credentials and a fake provider.
4. **F04 — High/P0:** failed usage batches are discarded without retry or durable recovery; budgets depend on those rows.
5. **F05 — High/P0 for strict spend limits:** budgets/TPM do not reserve in-flight spend; unknown costs bypass budget sums. Exposure exceeds the documented 0.5-second window.
6. **F06 — High/P0 for stateful launch:** user and assistant writes commit separately; concurrent turns have no ordering/version contract.
7. **F07 — Medium/P1:** mid-stream outages emit a notice and normal completion, record success, and can persist incomplete exchanges. Verified.
8. **F08 — Medium/P1:** output redaction runs after schema validation; a 200 response can violate its requested schema. Verified.
9. **F09 — Medium/P1:** TOML/provider validation is lazy; missing config can coexist with successful health/readiness. Verified.
10. **F10 — High/P0 if console exposed:** Streamlit sessions inherit an admin token and can change the destination URL; built-in web routes bypass API auth when enabled. Default loopback/off settings mitigate exposure.

## Top ten architecture improvements

1. Use server-generated unique accounting IDs and separate client correlation IDs.
2. Use an offline schema registry and constrain validation complexity.
3. Check the fully assembled outbound context and tool payload under a defined data policy.
4. Separate durable spend accounting from best-effort analytics.
5. Reserve bounded request spend/tokens atomically when enforcing hard limits.
6. Persist exchanges atomically and define optimistic conversation ordering.
7. Carry an explicit terminal stream outcome through router, API, persistence, and metrics.
8. Validate configuration eagerly and apply an actual end-to-end deadline.
9. Bound DB queries, prompts, output, and admitted upstream concurrency.
10. Establish a repeatable release/restore process before adding replicas.

## Missing essentials and target

Missing essentials: TLS ingress configuration, private administrative access policy, schema migration history, tested backups, retention/deletion policy, dependency lock/scanning, production-path PostgreSQL tests, writer-loss alerts, agreed SLIs/SLOs, and live adapter verification. No implemented feature is certified production-ready end to end.

Target: TLS ingress → one hardened FastAPI gateway worker → PostgreSQL → provider adapters. Keep operator UI private and Prometheus/Grafana internal. Retain local cache and breakers initially. Add shared atomic admission state only when introducing multiple workers/replicas or contractual global limits. A PostgreSQL reservation ledger may be sufficient before adding Redis. No evidence justifies Kafka, Kubernetes, or microservices.

## First five tasks

1. Disable external JSON Schema retrieval and add offline abuse regressions (F01).
2. Separate correlation IDs from accounting IDs; isolate/recover failed batches (F02/F04).
3. Apply memory/context/tool data policy before outbound inference (F03).
4. Decide hard versus advisory budgets; implement reservations and unknown-cost handling (F05).
5. Make stateful exchanges atomic and reject/reconcile conflicting turns (F06).

## Decisions needed

Launch users and trust model; whether one project key is intentionally allowed all users' conversations; data sensitivity/provider residency; hard versus advisory budgets; supported provider/model versions and tool contracts; expected concurrent requests and stream duration; latency/availability SLOs; hosting/budget; retention and RPO/RTO. These were requested during the audit. Pending answers, recommendations assume a small private pilot on one host, no regulated data, and no guaranteed capacity. Estimates are planning ranges, not commitments.

## Report index

[Maintained architecture](../docs/ARCHITECTURE.md), [feature verification](01-feature-verification.md), [security](04-security-audit.md), [performance](05-performance-scalability.md), [reliability/observability](06-reliability-observability.md), [testing/DevOps](07-testing-devops.md), [gap checklist](08-production-gap-analysis.md), [historical roadmap](09-engineering-roadmap.md), [verification evidence](11-verification-evidence.md), [interview deep dive](12-interview-deep-dive.md).
