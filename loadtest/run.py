"""Closed-loop load generator for the gateway's /v1/chat/completions.

Each of N virtual users sends requests back-to-back for a fixed duration.
Reports RPS, latency percentiles, error rate, and - via the admin API, if
ADMIN_TOKEN is given - provider split and cache hit rate.

    python loadtest/run.py --url http://127.0.0.1:8000 --users 10 50 100 500 --duration 20

Use with a mock-provider config (loadtest/gateway.mock.toml) to measure the
gateway itself rather than a real provider's latency and quotas.
"""
import argparse
import asyncio
import os
from concurrent.futures import ProcessPoolExecutor
import statistics
import time
import uuid
from collections import Counter
from typing import List, Optional

import httpx


async def _user(client: httpx.AsyncClient, deadline: float, args, latencies: List[float], statuses: Counter) -> None:
    n = 0
    while time.monotonic() < deadline:
        n += 1
        if args.repeat_pool:
            prompt = f"question {uuid.uuid4().int % args.repeat_pool}"
        else:
            prompt = f"question {uuid.uuid4().hex}"
        body = {"model": args.model, "messages": [{"role": "user", "content": prompt}]}
        started = time.monotonic()
        try:
            response = await client.post("/v1/chat/completions", json=body)
            statuses[response.status_code] += 1
        except httpx.HTTPError as exc:
            statuses[type(exc).__name__] += 1
        latencies.append(time.monotonic() - started)


def _pct(values: List[float], p: int) -> float:
    return statistics.quantiles(values, n=100, method="inclusive")[p - 1] * 1000 if len(values) > 1 else float("nan")


async def _measure(args, users: int):
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    limits = httpx.Limits(max_connections=users, max_keepalive_connections=users)
    latencies: List[float] = []
    statuses: Counter = Counter()
    async with httpx.AsyncClient(base_url=args.url, headers=headers, timeout=60, limits=limits) as client:
        started = time.monotonic()
        deadline = started + args.duration
        await asyncio.gather(*(_user(client, deadline, args, latencies, statuses) for _ in range(users)))
        elapsed = time.monotonic() - started
    return latencies, statuses, elapsed


def _measure_in_process(args, users: int):
    return asyncio.run(_measure(args, users))


def run_level(args, users: int) -> dict:
    """Splits `users` across `args.processes` client processes: one Python
    process can't drive hundreds of concurrent connections without itself
    becoming the bottleneck."""
    processes = max(1, min(args.processes, users))
    shares = [users // processes + (1 if i < users % processes else 0) for i in range(processes)]
    latencies: List[float] = []
    statuses: Counter = Counter()
    elapsed = 0.0
    with ProcessPoolExecutor(max_workers=processes) as pool:
        for lat, st, el in pool.map(_measure_in_process, [args] * processes, shares):
            latencies.extend(lat)
            statuses.update(st)
            elapsed = max(elapsed, el)
    total = sum(statuses.values())
    errors = total - statuses.get(200, 0)
    return {
        "users": users,
        "requests": total,
        "rps": total / elapsed,
        "p50": _pct(latencies, 50),
        "p95": _pct(latencies, 95),
        "p99": _pct(latencies, 99),
        "error_rate": errors / total if total else 0.0,
        "statuses": dict(statuses),
    }


def _usage(args) -> Optional[dict]:
    if not args.admin_token:
        return None
    response = httpx.get(
        f"{args.url}/admin/usage",
        params={"hours": 1, "group_by": "provider"},
        headers={"Authorization": f"Bearer {args.admin_token}"},
        timeout=30,
    )
    return response.json() if response.status_code == 200 else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--users", type=int, nargs="+", default=[10, 50, 100, 500])
    parser.add_argument("--duration", type=float, default=20)
    parser.add_argument("--model", default="auto")
    parser.add_argument("--processes", type=int, default=os.cpu_count() or 1, help="client processes")
    parser.add_argument("--repeat-pool", type=int, default=0, help="draw prompts from N distinct ones (cache hits)")
    parser.add_argument("--api-key", default=os.environ.get("GATEWAY_API_KEY", ""))
    parser.add_argument("--admin-token", default=os.environ.get("ADMIN_TOKEN", ""))
    args = parser.parse_args()

    print("| users | requests | RPS | p50 ms | p95 ms | p99 ms | error rate | statuses |")
    print("|---|---|---|---|---|---|---|---|")
    for users in args.users:
        r = run_level(args, users)
        print(
            f"| {r['users']} | {r['requests']} | {r['rps']:.1f} | {r['p50']:.0f} | {r['p95']:.0f} | "
            f"{r['p99']:.0f} | {r['error_rate']:.2%} | {r['statuses']} |",
            flush=True,
        )

    usage = _usage(args)
    if usage:
        print("\nProvider split (admin API, last hour):")
        for group in usage["groups"]:
            print(
                f"  {group['provider']}: requests={group['requests']} errors={group['errors']} "
                f"fallbacks={group['fallbacks']} cache_hits={group['cache_hits']} "
                f"avg_latency_ms={group['avg_latency_ms']}"
            )
        print(f"  cache hit rate: {usage['total']['cache_hit_rate']:.2%}")


if __name__ == "__main__":
    main()
