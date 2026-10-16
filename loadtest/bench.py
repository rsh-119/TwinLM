"""Repeatable whole-stack benchmark against a throwaway Compose project.

    python loadtest/bench.py [--users 1 10 50 100] [--duration 15] [--out bench.json] [--instances 2]

Starts its own project (`twinlm-bench`, gateway on 127.0.0.1:18000, mock
providers from loadtest/gateway.bench.toml, PostgreSQL), measures, and
removes the project and its volumes at the end. It never touches another
Compose project. Measures:

- cold start (fresh volumes, incl. PostgreSQL init) and gateway restart to /ready
- idle CPU/memory per container
- gateway-added latency: sequential requests to a zero-latency mock
- throughput/latency at several concurrency levels (50 ms mock)
- provider outage: failing primary -> retry, breaker, fallback
- database outage: PostgreSQL stopped for 10 s under load, then restarted

`--instances 2` runs two gateways behind a Caddy load balancer, sharing
PostgreSQL and Redis (loadtest/compose.scale.yml), on the same port.

Results describe the machine they ran on, not a capacity guarantee.
"""
import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from argparse import Namespace
from collections import Counter

import httpx

sys.path.insert(0, os.path.dirname(__file__))
from run import run_level  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = "twinlm-bench"
URL = "http://127.0.0.1:18000"
FILES: list = []  # extra `-f` compose files (--instances 2)
GATEWAYS = ["gateway"]
ENV = {**os.environ, "GATEWAY_CONFIG_FILE": "./loadtest/gateway.bench.toml", "GATEWAY_PORT": "18000",
       "COMPOSE_PROJECT_NAME": PROJECT, "LOG_LEVEL": "WARNING"}


def compose(*args, check=True, capture=False):
    result = subprocess.run(["docker", "compose", "-p", PROJECT, *FILES, *args], cwd=ROOT, env=ENV, check=check,
                            capture_output=capture, text=True)
    return result.stdout if capture else None


def wait_ready(timeout=180.0) -> float:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        try:
            if httpx.get(f"{URL}/ready", timeout=1).status_code == 200:
                return time.monotonic() - started
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("gateway did not become ready")


def container_stats() -> dict:
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"],
        capture_output=True, text=True, check=True,
    ).stdout
    return {name.removeprefix(f"{PROJECT}-"): {"cpu": cpu, "mem": mem.split(" / ")[0]}
            for name, cpu, mem in (line.split("\t") for line in out.splitlines()) if name.startswith(PROJECT)}


def pct(values, p):
    return round(statistics.quantiles(values, n=100, method="inclusive")[p - 1] * 1000, 1)


def sequential_latency(headers, model, n=300) -> dict:
    with httpx.Client(base_url=URL, headers=headers, timeout=30) as client:
        def timed(fn):
            started = time.perf_counter()
            fn()
            return time.perf_counter() - started
        for _ in range(20):  # warm-up
            client.post("/v1/chat/completions", json={"model": model, "messages": [{"role": "user", "content": "w"}]})
        chat = [timed(lambda i=i: client.post("/v1/chat/completions", json={
            "model": model, "messages": [{"role": "user", "content": f"q{i}"}]}).raise_for_status()) for i in range(n)]
        health = [timed(lambda: client.get("/health").raise_for_status()) for _ in range(n)]
    return {"requests": n, "chat_p50_ms": pct(chat, 50), "chat_p95_ms": pct(chat, 95), "chat_p99_ms": pct(chat, 99),
            "health_p50_ms": pct(health, 50), "health_p95_ms": pct(health, 95)}


async def timeline_under_db_outage(api_key, users=10, before=5, outage=10, after=20) -> dict:
    """Per-second status counts while PostgreSQL is stopped and restarted."""
    headers = {"Authorization": f"Bearer {api_key}"}
    seconds: dict = {}
    stop = time.monotonic() + before + outage + after
    started = time.monotonic()

    async def user(client):
        while time.monotonic() < stop:
            second = int(time.monotonic() - started)
            try:
                status = (await client.post("/v1/chat/completions", json={
                    "model": "auto", "messages": [{"role": "user", "content": "db outage"}]})).status_code
            except httpx.HTTPError as exc:
                status = type(exc).__name__
            seconds.setdefault(second, Counter())[status] += 1

    async def chaos():
        await asyncio.sleep(before)
        await asyncio.to_thread(compose, "stop", "postgres")
        await asyncio.sleep(outage)
        await asyncio.to_thread(compose, "start", "postgres")

    async with httpx.AsyncClient(base_url=URL, headers=headers, timeout=15) as client:
        await asyncio.gather(chaos(), *(user(client) for _ in range(users)))
    timeline = {s: dict(c) for s, c in sorted(seconds.items())}
    recovered = next((s for s in sorted(timeline) if s > before + outage
                      and set(timeline[s]) == {200}), None)
    return {"stopped_at_s": before, "restarted_at_s": before + outage, "first_all_ok_second": recovered,
            "timeline": timeline}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--users", type=int, nargs="+", default=[1, 10, 50, 100])
    parser.add_argument("--duration", type=float, default=15)
    parser.add_argument("--processes", type=int, default=2)
    parser.add_argument("--out", default="")
    parser.add_argument("--instances", type=int, choices=[1, 2], default=1)
    args = parser.parse_args()
    if args.instances == 2:
        FILES[:] = ["-f", "docker-compose.yml", "-f", "loadtest/compose.scale.yml"]
        GATEWAYS[:] = ["gateway", "gateway2"]
    results: dict = {"machine": {"cpus": os.cpu_count(), "platform": sys.platform}, "instances": args.instances}

    compose("down", "-v", "--remove-orphans", check=False, capture=True)
    try:
        compose("build", "-q", *GATEWAYS)
        started = time.monotonic()
        compose("up", "-d", "--quiet-pull")
        wait_ready()
        results["cold_start_to_ready_s"] = round(time.monotonic() - started, 2)
        started = time.monotonic()  # stop + start + app startup
        compose("restart", *GATEWAYS)
        wait_ready()
        results["gateway_restart_to_ready_s"] = round(time.monotonic() - started, 2)

        time.sleep(20)
        results["idle"] = container_stats()

        admin = compose("exec", "-T", "gateway", "cat", "/run/secrets/admin_token", capture=True).strip()
        key = httpx.post(f"{URL}/admin/keys", json={"name": "bench", "project": "bench"},
                         headers={"Authorization": f"Bearer {admin}"}, timeout=10).json()["key"]
        headers = {"Authorization": f"Bearer {key}"}

        results["gateway_added_latency"] = sequential_latency(headers, "overhead")

        load = Namespace(url=URL, api_key=key, model="auto", duration=args.duration, repeat_pool=0,
                         processes=args.processes, admin_token="")
        results["concurrency"] = []
        for users in args.users:
            level = run_level(load, users)
            level = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in level.items()}
            results["concurrency"].append(level)
            print(json.dumps(level), flush=True)
        results["under_load"] = container_stats()

        outage = Namespace(**{**vars(load), "model": "outage"})
        results["provider_outage"] = {k: (round(v, 3) if isinstance(v, float) else v)
                                      for k, v in run_level(outage, 10).items()}
        usage = httpx.get(f"{URL}/admin/usage", params={"hours": 1, "group_by": "provider"},
                          headers={"Authorization": f"Bearer {admin}"}, timeout=30).json()
        results["provider_outage"]["served_by"] = {g["provider"]: g["requests"] for g in usage["groups"]}
        results["provider_outage"]["breaker"] = httpx.get(
            f"{URL}/admin/providers", headers={"Authorization": f"Bearer {admin}"}, timeout=10).json()

        results["database_outage"] = asyncio.run(timeline_under_db_outage(key))
        # usage rows written during the outage must reach request_logs afterwards
        for _ in range(60):
            outboxes = {g: json.loads(compose("exec", "-T", g, "python", "-m", "app.ops", "outbox", "status",
                                              capture=True)) for g in GATEWAYS}
            if all(o["pending"] == 0 for o in outboxes.values()):
                break
            time.sleep(1)
        results["database_outage"]["outbox_after"] = outboxes
        logged = json.loads(compose("exec", "-T", "gateway", "python", "-m", "app.ops", "verify", capture=True))
        sent = sum(sum(c.values()) for c in results["database_outage"]["timeline"].values())
        results["database_outage"]["outage_requests_sent"] = sent
        results["request_logs_total"] = logged["request_logs"]
    finally:
        compose("down", "-v", "--remove-orphans", check=False, capture=True)

    text = json.dumps(results, indent=1, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)


if __name__ == "__main__":
    main()
