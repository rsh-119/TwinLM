# Testing, deployment, and quality engineering

## Executed validation

Explicit process variables: DATABASE_URL=sqlite:///:memory:, AUTH_ENABLED=false, WEB_UI_ENABLED=true, PYTHONDONTWRITEBYTECODE=1.

```powershell
& .\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
```

**198 passed, 0 failed, 0 skipped, one warning, 6.17 seconds.** The warning is Starlette TestClient's deprecated anyio.abc.BlockingPortal alias. Coverage was **not measured**; the existing `.coverage` was neither trusted nor overwritten. No tests were changed. Tests use temporary/in-memory databases and fake/MockTransport providers; explicit env overrides prevent conftest's setdefault from inheriting a real DB URL. Existing tests create disposable tmp_path SQLite files, not application DB files.

`pip check` passed. AST parsing succeeded for all **58 Python files** in app/tests/ui/loadtest/examples/scripts. `docker compose --env-file .env.example config --quiet` exited successfully, without starting services or displaying resolved secrets. Docker CLI available: 29.6.2. No Docker build, daemon-state inspection, service startup, migration against application data, SDK live call, provider request, external vulnerability scan or load test was run.

## Test quality

| Layer | Existing evidence | Assessment / missing proof |
|---|---|---|
| Pure/unit | Context truncation, cache TTL/LRU, pricing/limits, breaker/backoff | Useful behavioral assertions; no summarizer quality/retention regression |
| Router resilience | Fake clock/timeouts, retry bound, malformed transport, cancellation and half-open slot (`test_resilience.py`) | Strong isolated failure contracts; concurrent breaker outcomes not established |
| API | TestClient chat/auth/project scoping/usage/guardrails/schema/SSE (`test_chat.py`, `test_gateway_api.py`, `test_openai_compat.py`) | Meaningful outputs and provider-call checks; some tests lock in incomplete stream behavior |
| Adapter contracts | `test_providers.py` MockTransport payload/finish/tools/images | Tests own chosen fixtures, not real provider conformance; no broad actual HTTP error matrix |
| DB | SQLite CRUD, old additive schema, pool regression | No current PostgreSQL integration/isolation/migration/restore test |
| Production writer | One 250-row happy-path background flush (`test_gateway_api.py:669`) | Most services have writer=None; no duplicate batch/DB outage/overflow/restart tests |
| Security | Cross-project CRUD denial, missing/revoked key, XSS sanitizer | Missing SSRF registry, payload bounds, memory bypass, console policy and final schema regressions |
| End to end | Historical docs claim SDK/Compose exercises | Unverified now; no automated container/SDK real socket gate |
| Concurrency | 20 concurrent ASGI requests, pool two | Valuable deadlock regression; not stateful conflict/spend reservation correctness |
| Load/fault | Historical load harness/docs | Closed-loop nonstream only; no current production-like measured capacity |

Fake providers are appropriate for offline business logic; their simple success/error contract misses HTTPStatusError mapping and production persistence behavior. In-memory StaticPool differs from PostgreSQL and ordinary multi-connection SQLite. Most API tests disable auth except explicit cases and enable the loginless web UI at import. Thus test pass does not establish deployment-safe defaults; startup auth default has its own regression. Slow timeout tests are deliberately bounded, not evidence of a hanging suite. No repeated-run flakiness study performed. Random load-provider failures make benchmark runs nondeterministic; seed or deterministic schedule for regression runs.

Recommended pyramid: broad fast pure/router tests; focused PostgreSQL/production-writer integration tests; small socket/SDK/container smoke suite; release-only controlled load/restore/chaos tests. Prioritize regressions from F01-F09 and transaction/reservation correctness before increasing test count. Avoid mock assertions that mirror implementation instead of public behavior.

## Container and deployment review

Gateway Dockerfile uses python:3.11-slim, dependency layer before source, non-root uid 10001, writable SQLite directory, bytecode disabled, readiness healthcheck and exec-form uvicorn. UI has non-root uid 10002 and Streamlit healthcheck. No privileged mode or Docker socket mount found. Single-stage pure Python builds are reasonable here; multi-stage is useful only for measurable image/dependency reduction. Image size was not measured. Runtime includes pytest/dev and optional web rendering packages; split dependencies if image size/security maintenance justify it.

Compose binds host ports to loopback, sets auth on and built-in web UI off, mounts config read-only, waits for secret generator/PostgreSQL/gateway health, persists four volumes and restarts long-lived services. This is a credible local development recipe, not demonstrated production HA. Secret generator chmod 444 exposes all secrets to each mounted service; startup pipeline lacks robust failure checking/atomic creation. No secret rotation/backup policy, per-service network segmentation, CPU/memory limits, logs rotation, TLS ingress, data backup or graceful release procedure exists. Standalone SQLite needs an explicit volume; image directory alone does not preserve a removed container. README .env.example DATABASE_URL overrides the Dockerfile SQLite path if passed through env-file; confirm volume/write permissions in the smoke test.

Dependencies are unpinned; Python 3.13.2 locally versus 3.11 CI/container is an unverified environment difference. ui/requirements allows streamlit>=1.40 while current code uses modern UI arguments; lock a tested version. Base/Compose images and action tags are mutable, no SBOM/scanner/signing/lockfile. No confirmed vulnerable dependency claim follows from `pip check`, which only checks dependency compatibility.

## CI/CD and release

`.github/workflows/ci.yml:9-50` runs Python 3.11 pytest, then builds/pushes GHCR on push if tests pass; image tags include SHA/ref/version/latest. Workflow is untracked in the starting tree. Its execution and GHCR visibility were not verified. There is no lint/type gate, PostgreSQL test job, PR container build, security scan, image startup check, migration job, deployment automation, rollback validation or backup gate. Pin actions/dependency inputs, test both minimum and supported runtime if desired, build smoke on PR, scan image, deploy immutable digest after migration/backup checks. CI publishing is not deployment automation.

A different engineer can follow the private Compose startup instructions, but reliable public deployment requires undocumented decisions: TLS/DNS, permissions and shared console access, provider key/pricing verification, model support, persistent storage, migrations, backups/restores, host sizing and alert ownership. Documentation claims of prior full-stack success are historical and conflict in places with current defaults.

Practical release strategy: small VM/private ingress plus managed PostgreSQL if affordable; internal metrics/console. Migrate once with a serialized job; use expand/contract schema changes; launch pinned image/config; wait initialized readiness; run synthetic tenant/auth/chat/SSE checks; drain old process; retain prior image/config. Single-host Compose restart can cause downtime; do not claim zero downtime. Two replicas require correct shared quotas/reservations, stream draining and database compatibility before rolling deploys. Kubernetes is not justified by current evidence.
