"""Operator commands. Run inside the gateway container (or with the same
DATABASE_URL / USAGE_SPOOL_PATH):

    python -m app.ops migrate [--check]       apply (or check for) schema migrations
    python -m app.ops verify                  schema version + row counts (restore drills)
    python -m app.ops retention --request-logs-days 90 --conversations-days 365 [--memories-days N] [--dry-run]
    python -m app.ops outbox status|list|reconcile|requeue|export FILE
    python -m app.ops create-admin --email you@company.com --name "Your Name"   first console admin

Procedures that use these are in docs/OPERATIONS.md.
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from app.config import get_settings
from app.database import database
from app.database.models import ApiKey, Conversation, Memory, Message, RequestLog

TABLES = {"conversations": Conversation, "messages": Message, "memories": Memory,
          "api_keys": ApiKey, "request_logs": RequestLog}


def migrate(check: bool) -> int:
    with database.engine.connect() as conn:
        current = database.schema_version(conn)
    print(f"schema version {current}, code expects {database.SCHEMA_VERSION}")
    if check:
        return 0 if current == database.SCHEMA_VERSION else 1
    print(f"applied: {database.init_db() or 'nothing'}")
    return 0


def verify() -> int:
    with database.engine.connect() as conn:
        report = {"schema_version": database.schema_version(conn)}
        report.update({name: conn.execute(select(func.count()).select_from(model)).scalar()
                       for name, model in TABLES.items()})
    print(json.dumps(report))
    return 0 if report["schema_version"] == database.SCHEMA_VERSION else 1


def retention(request_logs_days, conversations_days, memories_days, dry_run: bool) -> int:
    """Deletes rows older than the given ages; omitted = keep forever.
    Conversations age by last update and take their messages with them."""
    now = datetime.now(timezone.utc)
    cutoff = lambda days: (now - timedelta(days=days)).replace(tzinfo=None)  # noqa: E731 - columns are naive UTC
    plan = []
    if request_logs_days is not None:
        plan.append(("request_logs", RequestLog, RequestLog.created_at < cutoff(request_logs_days)))
    if conversations_days is not None:
        stale = select(Conversation.id).where(Conversation.updated_at < cutoff(conversations_days))
        plan.append(("messages", Message, Message.conversation_id.in_(stale)))
        plan.append(("conversations", Conversation, Conversation.id.in_(stale)))
    if memories_days is not None:
        plan.append(("memories", Memory, Memory.created_at < cutoff(memories_days)))
    with database.engine.begin() as conn:
        for name, model, condition in plan:
            count = conn.execute(select(func.count()).select_from(model).where(condition)).scalar()
            if not dry_run:
                conn.execute(delete(model).where(condition))
            print(f"{name}: {'would delete' if dry_run else 'deleted'} {count}")
    return 0


def outbox(action: str, path=None) -> int:
    """The local usage outbox (USAGE_SPOOL_PATH): `pending` rows wait for
    the main database; `deadletter` rows failed with a data error."""
    path_ = get_settings().usage_spool_path
    spool = sqlite3.connect(f"file:{path_}?mode=rw", uri=True, timeout=5)  # never create an empty spool
    try:
        if action == "status":
            counts = {t: spool.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("pending", "deadletter")}
            print(json.dumps(counts))
        elif action == "list":
            for key, payload in spool.execute("SELECT id, payload FROM deadletter ORDER BY id"):
                print(key, payload)
        elif action == "export":
            with open(path, "w", encoding="utf-8") as out:
                for (payload,) in spool.execute("SELECT payload FROM deadletter ORDER BY id"):
                    out.write(payload + "\n")
            print(f"exported to {path}")
        elif action == "reconcile":
            # a deadletter row whose ID is already in request_logs was recorded
            # (e.g. replayed after a crash); drop it, keep the rest for review
            ids = [key for (key,) in spool.execute("SELECT id FROM deadletter")]
            with database.engine.connect() as conn:
                recorded = {key for (key,) in conn.execute(select(RequestLog.id).where(RequestLog.id.in_(ids)))} if ids else set()
            spool.executemany("DELETE FROM deadletter WHERE id = ?", [(key,) for key in recorded])
            spool.commit()
            print(json.dumps({"already_recorded_removed": len(recorded), "remaining": len(ids) - len(recorded)}))
        elif action == "requeue":
            # after fixing the cause (e.g. a migration), retry deadletters;
            # rows that still fail return to deadletter
            moved = spool.execute(
                "INSERT OR IGNORE INTO pending (id, payload, api_key_id, created_at, cost_usd) "
                "SELECT id, payload, json_extract(payload, '$.api_key_id'), json_extract(payload, '$.created_at'), "
                "json_extract(payload, '$.cost_usd') FROM deadletter"
            ).rowcount
            spool.execute("DELETE FROM deadletter WHERE id IN (SELECT id FROM pending)")
            spool.commit()
            print(f"requeued {moved}; the running gateway retries them on its next flush")
    finally:
        spool.close()
    return 0


def create_admin(email: str, name: str) -> int:
    """A console admin with a temporary password (printed once)."""
    from app.console_auth import hash_password, temporary_password
    from app.database.models import User

    database.init_db(apply=get_settings().migrate_on_startup)
    with database.SessionLocal() as db:
        email = email.strip().lower()
        if db.query(User).filter(User.email == email).first():
            print(f"{email} already exists", file=sys.stderr)
            return 1
        password = temporary_password()
        db.add(User(email=email, name=name, role="admin", password_hash=hash_password(password),
                    must_change_password=True))
        db.commit()
    print(f"admin {email} created. Temporary password (change it at first sign-in): {password}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.ops", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    migrate_cmd = commands.add_parser("migrate")
    migrate_cmd.add_argument("--check", action="store_true", help="exit 1 if migrations are pending")
    commands.add_parser("verify")
    retention_cmd = commands.add_parser("retention")
    for name in ("request-logs", "conversations", "memories"):
        retention_cmd.add_argument(f"--{name}-days", type=int)
    retention_cmd.add_argument("--dry-run", action="store_true")
    outbox_cmd = commands.add_parser("outbox")
    outbox_cmd.add_argument("action", choices=["status", "list", "reconcile", "requeue", "export"])
    outbox_cmd.add_argument("path", nargs="?")
    admin_cmd = commands.add_parser("create-admin")
    admin_cmd.add_argument("--email", required=True)
    admin_cmd.add_argument("--name", default="Admin")
    args = parser.parse_args(argv)

    if args.command == "migrate":
        return migrate(args.check)
    if args.command == "verify":
        return verify()
    if args.command == "create-admin":
        return create_admin(args.email, args.name)
    if args.command == "retention":
        days = (args.request_logs_days, args.conversations_days, args.memories_days)
        if any(d is not None and d < 1 for d in days):
            parser.error("retention days must be >= 1")
        return retention(*days, dry_run=args.dry_run)
    if args.action == "export" and not args.path:
        parser.error("outbox export needs a file path")
    return outbox(args.action, args.path)


if __name__ == "__main__":
    sys.exit(main())
