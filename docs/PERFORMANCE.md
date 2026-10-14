# Performance

All numbers come from real runs and describe **this machine and setup**,
not a capacity guarantee. Mock providers isolate the gateway; real
providers' latency and quotas will dominate real traffic.

## Current: whole stack, 14 October 2026

| | |
| --- | --- |
| Host | Windows 11, 4 logical CPUs; Docker Desktop VM with 4 CPUs / 3.7 GiB; load generator on the same host (2 client processes) |
| Stack | `docker compose` minimal set: 1 gateway container (1 uvicorn worker, Python 3.11), PostgreSQL 16, auth on, usage outbox on |
| Providers | `loadtest/gateway.bench.toml`: `instant` (0 ms mock), `slow50` (50 ms mock), `down` (always fails) |
| Tool | `python loadtest/bench.py` (throwaway Compose project; raw results in [`benchmarks/2026-10-14.json`](benchmarks/2026-10-14.json)) |

| Measure | Result |
| --- | --- |
| Idle memory / CPU | gateway 76 MiB, PostgreSQL 33–69 MiB; both < 0.3% CPU |
| Cold start to `/ready` (fresh volumes, incl. PostgreSQL init) | 13–16 s (3 runs) |
| Gateway restart to `/ready` (stop + start) | 4.8–10.3 s (3 runs) |
| Gateway-added latency (sequential, 0 ms provider) | p50 10.1 ms, p95 18.8 ms, p99 27.1 ms (`/health`: p50 2.3 ms) |

Concurrency (50 ms provider, 15 s per level):

| Users | Requests | RPS | p50 ms | p95 ms | p99 ms | Errors |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 210 | 13.9 | 65 | 99 | 197 | 0% |
| 10 | 1169 | 77.4 | 122 | 187 | 247 | 0% |
| 50 | 1610 | 105.2 | 432 | 739 | 886 | 0% |
| 100 | 1900 | 122.3 | 762 | 1126 | 1354 | 0% |

Failure behavior:

- **Provider outage** (`outage` alias: always-failing primary, then the 50 ms
  mock; 10 users): 1022/1022 succeeded, p50 131 ms. The primary's circuit
  opened after 7 failed attempts and traffic went to the fallback.
- **Database outage** (PostgreSQL stopped for 10 s under 10 users of
  stateless chat): **0 failed requests** (2469 sent). Cached key validation
  kept authentication working and usage rows waited in the local outbox.
  Afterwards the outbox drained (0 pending, 0 deadletter) and
  `request_logs` held exactly one row per request sent by the benchmark
  (8700 of 8700). Stateful turns, key creation and budgeted keys still need
  the database and return `503 database unavailable` during an outage.

**Ceiling:** about 95–125 RPS for one worker on this host. At 100 users
the gateway container sampled 80–100% of one core in `docker stats` while
PostgreSQL sampled 9%; latency beyond that point is queueing. More throughput means more workers or
instances, which first needs shared admission state (see ARCHITECTURE.md).
The earlier ~200 RPS figure (below) was measured with native Python 3.13
and SQLite on the host, not this containerized PostgreSQL stack; the two are
not comparable.

### What this round found and fixed

Same benchmark before the fixes
([`benchmarks/2026-10-14-before-fixes.json`](benchmarks/2026-10-14-before-fixes.json)):

| | Before | After |
| --- | --- | --- |
| 10 users | 62.0 RPS, p50 143 ms | 77.4 RPS, p50 122 ms |
| 50 users | 66.5 RPS, p50 725 ms | 105.2 RPS, p50 432 ms |
| 100 users | **60% errors** (`503 server busy`), 106 RPS incl. errors | 0% errors, 122.3 RPS |
| 10 s database outage | 500s and dropped connections; all-OK again 9 s after restart | 0 errors |
| Gateway-added latency p50 / p95 | 14.4 / 27.6 ms | 10.1 / 18.8 ms |

1. **Connection churn.** The default SQLAlchemy pool (5 + 10 overflow)
   discards overflow connections, so under load most requests opened a new
   PostgreSQL connection (PostgreSQL at ~75% CPU for ~110 RPS). Now a
   persistent pool (`DB_POOL_SIZE`, default 20) with `pool_pre_ping`
   (replaces connections killed by a restart) and a 3 s connect timeout.
2. **One database round trip per request for auth.** Validated keys are
   cached for `AUTH_CACHE_SECONDS` (30 s); revoke/rotate evict immediately
   on this instance, and a stale entry is used only while the database is
   unreachable.
3. **Spurious "server busy".** The in-flight limiter released its slot after
   dependency teardown, which queues on the worker thread pool, so clients
   that had their answer were already sending the next request. Slots are
   now freed when the response body is complete.
4. **Opaque database errors.** `OperationalError` and pool timeouts now map
   to an OpenAI-shaped `503 database unavailable` with `Retry-After: 5`
   instead of a plain-text 500.

Reproduce: `python loadtest/bench.py --users 1 10 50 100 --duration 15`
(needs Docker; touches only the `twinlm-bench` project).

## History: 4 October 2026 (host, SQLite)

Earlier runs used native Python 3.13 on the host, SQLite, `loadtest/gateway.mock.toml`
(50 ms mock with 2% failures) and `loadtest/run.py` with 3 client
processes: about 96 RPS at 10 users, 204 at 50, 193 at 100. They found and
fixed a DB-pool deadlock (`release_connection` before provider awaits;
regression test `test_concurrent_requests_do_not_exhaust_the_db_pool`) and
moved usage writes off the event loop (`UsageWriter`). Those numbers are
from an older working tree and a different stack; do not compare them with
the table above.

Not measured: real providers, multi-worker deployments, hosts other than
this one.
