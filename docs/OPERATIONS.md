# Operating TwinLM

Procedures for the supported baseline: **one gateway instance + PostgreSQL**
under Docker Compose. Architecture decisions live in
[ARCHITECTURE.md](ARCHITECTURE.md); this file is the runbook. Commands assume
the repository root and the default Compose project. Never run drills or
destructive commands against a database you cannot afford to lose; the
restore drill below uses a throwaway container.

Inputs only you can provide are marked **EXTERNAL**.

## Deploy

```sh
cp .env.example .env              # provider keys; switches (see comments)
docker compose up -d --build      # secrets + postgres + gateway (minimal)
docker compose exec gateway cat /run/secrets/admin_token
```

Optional profiles: `--profile console` (Streamlit test console),
`--profile monitoring` (Prometheus with `monitoring/alerts.yml` + Grafana),
`--profile ingress` (Caddy TLS, below). Ports bind to 127.0.0.1.

Keys: create a **service key** per application (`POST /admin/keys` with
`name`, `project`) and a **user-bound key** per person when people share a
project (`"user_id": "alice"`). User-bound keys only see their own
conversations and memories. Set `STATEFUL_REQUIRES_USER_KEYS=true` to stop
service keys from touching conversations/memories at all;
`STATEFUL_ENABLED=false` runs a pure stateless proxy.

## Shared access: TLS ingress

**EXTERNAL:** DNS names for the API (and optionally console/Grafana) pointing
at the host, ports 80/443 reachable for Let's Encrypt, and a decision about
who gets console access. There is no SSO/OIDC integration; if you need one,
put an identity-aware proxy in front instead of basic auth.

```sh
# .env
TWINLM_DOMAIN=api.example.com
CONSOLE_DOMAIN=console.example.com
GRAFANA_DOMAIN=grafana.example.com
docker compose --profile ingress --profile console --profile monitoring up -d
docker compose exec caddy cat /run/secrets/console_password   # basic-auth password (user: operator)
```

`deploy/Caddyfile` exposes **only `/v1/*`** on the API host (every call needs
a gateway key). `/admin`, `/metrics` and `/docs` are never proxied: use the
console (server-side, inside the Compose network) or an SSH tunnel to
`127.0.0.1:8000`. Console and Grafana sit behind HTTP basic auth over TLS.
Keep `GATEWAY_BIND`, `UI_BIND`, `GRAFANA_BIND`, `PROMETHEUS_BIND` on
127.0.0.1. Without real DNS, the `*.localhost` defaults use Caddy's internal
CA for local testing.

## Schema migrations

Migrations are versioned, forward-only and recorded in `schema_migrations`
(`app/database/database.py`). The gateway applies pending ones at startup
under a database lock, refuses to start against a schema **newer** than it
knows, and, with `MIGRATE_ON_STARTUP=false`, refuses to start against an
older one.

Upgrade procedure:

1. Back up (below).
2. `docker compose run --rm --no-deps --entrypoint python gateway -m app.ops migrate --check` with the **new** image (exit 1 = migrations pending).
3. Deploy the new image; it migrates on start (or, with `MIGRATE_ON_STARTUP=false`, run the same command with `migrate` instead of `migrate --check` first).
4. `docker compose exec gateway python -m app.ops verify`.

Rollback = restore the pre-upgrade backup and the previous image. There are no
down-migrations. Migration 2 (this release) only adds nullable columns
(`api_keys.user_id`, `request_logs.deployment`); the previous release has no
schema-version check and runs on the upgraded schema, but it ignores
`user_id`, so user-bound keys silently become service keys. Restore the
backup if you roll back after issuing user-bound keys.

## Backup and restore

```sh
scripts/backup.sh ./backups      # pg_dump -Fc + consistent outbox copy + sha256
```

**EXTERNAL:** an off-host destination, encryption key and schedule. Example
with [age](https://age-encryption.org) and a cron entry:

```sh
age -r "$BACKUP_RECIPIENT" -o twinlm-<stamp>.dump.age backups/twinlm-<stamp>.dump
# then copy the .age files off-host (object storage, another machine) and delete local plaintext
```

Restore **drill** (safe; throwaway container, verifies schema version and row
counts with the gateway image):

```sh
scripts/restore_drill.sh backups/twinlm-<stamp>.dump twinlm-gateway
```

Real restore (destructive; replaces live data):

```sh
docker compose stop gateway
docker compose exec -T postgres pg_restore -U twinlm -d twinlm --clean --if-exists --no-owner < backups/twinlm-<stamp>.dump
docker compose start gateway
docker compose exec gateway python -m app.ops verify
```

Usage rows still in the outbox at backup time are in `usage-spool-<stamp>.db`;
restoring it to `/app/data/usage-spool.db` (gateway stopped) replays them.
Replays deduplicate by request ID.

## Retention and deletion

**EXTERNAL:** your retention policy. Nothing is deleted automatically.
Request logs contain no prompt text; conversations and memories do.

```sh
docker compose exec gateway python -m app.ops retention --request-logs-days 400 --conversations-days 180 --dry-run
docker compose exec gateway python -m app.ops retention --request-logs-days 400 --conversations-days 180
```

Conversations age by last update and take their messages with them;
`--memories-days` ages memory facts by creation. A single user's data is
removed with `DELETE /v1/conversations/{id}` per conversation; there is no
bulk user-erasure endpoint yet. Backups keep deleted data until they expire,
so align backup retention with this policy.

## Key rotation

| Secret | Rotate |
| --- | --- |
| Gateway API key | `POST /admin/keys/{id}/rotate` returns a new key with the same project, user, limits and budget. The old key keeps working until you revoke it (`DELETE /admin/keys/{id}`, or `?revoke_old=true` on rotate). |
| Admin token | Write a new value to the `secrets` volume (`docker compose run --rm secrets sh -c 'rm /secrets/admin_token'` then `docker compose up -d secrets gateway`), update operators. |
| Provider keys | Add the new key in the provider console, update `.env`, `docker compose up -d gateway`, then revoke the old key at the provider. Two credentials for one model can run side by side as two providers in one model group (`gateway.toml`). |
| PostgreSQL password | `ALTER USER twinlm PASSWORD '...'` inside postgres, write the same value to `/secrets/postgres_password`, restart gateway. |
| Console password | Delete `/secrets/console_password`, rerun `secrets`, restart `caddy`. |

## Usage outbox reconciliation

Each request's usage row is committed to a local SQLite outbox
(`/app/data/usage-spool.db` on the `gateway-data` volume) and copied to
`request_logs` in the background. Alerts: `TwinLMUsageBacklogGrowing`,
`TwinLMUsageDeadletter`, `TwinLMUsageWriteFailures`.

```sh
docker compose exec gateway python -m app.ops outbox status       # pending / deadletter counts
docker compose exec gateway python -m app.ops outbox list         # deadletter rows (no prompt text)
docker compose exec gateway python -m app.ops outbox reconcile    # drop deadletters already in request_logs
docker compose exec gateway python -m app.ops outbox requeue      # after fixing the cause: retry them
docker compose exec gateway python -m app.ops outbox export /app/data/deadletter.jsonl
```

A growing `pending` count means the database is unreachable; it drains on its
own once the database is back. Deadletters are rows the database rejected
(data errors); fix the cause (often a pending migration), then `requeue`.
Do not delete the volume while rows are pending.

## Monitoring and alerts

`--profile monitoring` starts Prometheus with `monitoring/alerts.yml`
(gateway down, error rate, open circuit, outbox backlog/deadletter/write
failures, unpriced calls) and Grafana. Prometheus evaluates the rules; it does
not notify anyone. **EXTERNAL:** an Alertmanager with a receiver (email,
Slack, pager) if you want notifications.

## Costs and budgets

Costs are estimates from `[pricing]` in `gateway.toml` (verify against your
provider's price list) and provider-reported usage. Unknown stays unknown:
missing prices or usage give `cost_usd = null`, `/admin/usage` reports
`unpriced_requests` and `cost_complete`, and `llm_unpriced_calls_total`
counts them. Reconcile monthly against provider invoices.

`daily_budget_usd` is **advisory**: it rejects a key's requests once settled
plus pending known cost reaches the budget. Concurrent requests can overshoot,
and unknown-cost requests are not counted. Use provider-side spend limits as
the hard stop.
