# Performance and scalability

## Evidence boundary

No load test was run in this audit. `docs/PERFORMANCE.md:1-49` records historical mock runs dated 2026-10-04: Windows 11, 4 logical CPUs, local Python 3.13.2, one uvicorn worker, SQLite, auth enabled, client and server sharing hardware, 15-second closed-loop levels. Reported 10/50/100/500 users achieved 96.4/204.3/192.7/80.5 RPS; 500 users had five connection errors. These are **documented historical results, not remeasured capacity**. No raw run output, CPU profile or matching immutable revision was supplied. Cache hit throughput barely changed historically; that supports a gateway/client ceiling hypothesis, not proof that specific CPU functions dominate. Concurrent users are not equivalent to concurrent requests.

Offline suite: 198 tests in 6.17s. It validates behavior, not performance. A 20-request pool regression with pool size two is useful correctness evidence (`tests/test_gateway_api.py:604`), not a PostgreSQL saturation benchmark.

## Likely bottlenecks (Inferred)

| Path | Structural evidence | Scaling consequence | Measurement needed |
|---|---|---|---|
| Stateless | Auth DB lookup; per-budget SUM; sync calls in async admission | Event-loop blocking/pool waits before upstream limits | Loop lag, checkout wait, SQL timings, CPU profile |
| Stateful | `repository.py:99` reads all messages; memory all(); save twice; inline summary | Increasing per-turn memory/DB/latency with history | History-size sweep and query EXPLAIN |
| Structured | Schema validation sync; repair restarts route deadline | CPU and extra upstream generation; malicious refs | Schema size/depth sweep offline with strict bounds |
| Streaming | `chat.py:352-362` collects whole text; no duration/output cap | Active connections and retained output dominate memory | Slow-client, output-size and duration sweeps |
| Writer | 100k row queue, one thread, batches 500; errors discard | Backlog, memory growth, missing spend under DB trouble | Queue age/depth, flush time, loss and recovery counts |
| Local limiter | Per-key deques; sums token window; no inactive-key eviction | Memory follows requests/key history; quotas multiply across workers | Key churn, max-rate and multiworker fairness |
| Cache | Entry cap, no byte cap; no single-flight | Large cached answers consume RAM; stampede on cold key | Byte size, hit rate, cold burst and eviction |
| Upstream | Pooled AsyncClient defaults; no per-provider semaphore | Pool wait/timeouts and provider RPM/TPM limits | Pool occupancy/queueing and provider 429 rate |
| Admin/UI | Python percentile samples; N+1 sidebar; full markdown on every stream update | Operator queries/rendering can contend with inference | Separate admin query and UI response profiles |

The first bottleneck depends on workload: real providers may hit quota or generation latency first; mock stateless paths likely saturate gateway scheduling/DB; long stateful histories likely expose synchronous reads earlier. There are no measured CPU/RAM/pool/DB latency results for this working tree. Do not size instances from the historical mock ceiling.

## Hypothetical concurrency scenarios

Assume one worker and one PostgreSQL database, auth on, bounded request sizes. No numeric RPS/latency promise follows.

| Concurrent active requests | Expected pressure (Inferred) | Required gate |
|---:|---|---|
| 10 | Plausible private pilot; provider quota and long histories can still fail | All P0 fixed, restore test, real-adapter smoke, baseline metrics |
| 100 | Pool/loop scheduling, provider quotas, reservations and streaming duration matter | Representative stateless/stateful/stream soak; controlled admission and load shedding |
| 1,000 | Current unbounded in-flight and synchronous DB are substantial risks | Shared admission if replicas; explicit resource/connection budgets; independent load host |
| 10,000 | Unsupported by evidence; connection egress, quotas, DB/host reliability major constraints | Establish actual user/request arrival model; staged distributed testing after lower levels pass |

Little's law planning: in-flight requests ≈ arrival rate × time in service. Slow generation can mean high concurrency at modest RPS. Retries amplify outbound request volume; cache only helps eligible stateless nonstream exact matches. Token budget and provider account quotas, not just request count, constrain inference. Cold starts initialize modules/schema and lazily create clients/config; there are no model weights to load. Horizontal replicas preserve DB data but reset local policies; add shared quotas before relying on global limits. Shared caches/breakers are optional independently.

## Reproducible benchmark plan (proposed, not executed)

1. Use an isolated test deployment with synthetic data, explicit mock-only config, test-only database, auth enabled, fixed model/config/dependency revisions. Never point these commands at production or paid providers without approval. Client/server on separate hosts; record CPU/RAM/OS/Python/runtime worker count, DB instance/volume configuration.
2. Warm up two minutes, then measure at least five minutes per agreed level and repeat three times. Start with 10 then 50/100 active requests; increase only when error/latency/resource gates pass. Add a 30-minute soak and bounded overload/recovery phase. Values are proposed test durations, not SLOs.
3. Existing `python loadtest/run.py --url http://127.0.0.1:8000 --users 10 50 100 --duration 300 --processes 3` supplies closed-loop nonstream unique prompts. Use a freshly created **test** key via environment and explicit mock deployment. `--repeat-pool 200` exercises cache; it is not a uniform real-world hit model. The script counts status and client errors but does not assert semantic correctness, capture resource use, stream, or isolate one run's admin aggregation window.
4. Extend a k6/Locust harness with an open arrival-rate scenario to expose queuing, actual SSE parsing/TTFT, 1/10/1000-history stateful turns, embedding batch sizes, schema repairs, mixed tenant quotas, multiple keys, slow consumers and cancellation. Verify actual response/tool/schema outcomes, not just HTTP 200.
5. Inject mock 429/5xx/connect timeouts and midstream drops; in disposable infrastructure only, pause DB or constrain disk. Check writer reconciliation, graceful shutdown, recovery and bounded queues. Gate data-loss/error correctness independently of latency.
6. Capture RPS; p50/p95/p99 latency by endpoint/workflow; TTFT and stream completion latency; HTTP and semantic error rate; process CPU/RSS; event-loop lag; DB query/checkout latency and pool usage; writer depth/oldest age/drop/retry; cache eligible hit/miss/bytes; provider attempt/retry/fallback and reserved/settled tokens. Persist raw samples, Prometheus snapshot, command/env **names only**, revision/config hash and hardware metadata.
7. Define acceptance targets after requirements are decided. Require zero cross-project access, zero unreported usage loss, bounded overload rejection, expected recovery and no uncontrolled RSS growth. Select p95/p99/availability/RPO/RTO targets with the owner. Repeat only after material changes or a failed gate.

Optimization order: bound SQL/context/output → correct admission/accounting → profile production-like DB → change DB I/O model if justified → worker/replica scale with shared quotas. No evidence currently justifies a distributed streaming broker or semantic vector cache.
