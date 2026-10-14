"""Versioned migrations (SQLite always; PostgreSQL with TEST_DATABASE_URL,
in a disposable schema), retention, restore verification and outbox
reconciliation."""
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, text

from app import ops
from app.database import database, models  # noqa: F401 - registers models
from app.gateway.usage import UsageWriter


@contextmanager
def _engine(kind, tmp_path):
    if kind == "sqlite":
        engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
        yield engine
        engine.dispose()
        return
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    schema = "twinlm_mig_" + uuid.uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        admin.dispose()


def _v1_schema_with_data(engine):
    """The released v1 schema: today's tables minus migration-2 columns."""
    database.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE api_keys DROP COLUMN user_id"))
        conn.execute(text("ALTER TABLE request_logs DROP COLUMN deployment"))
        conn.execute(text("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
                          "applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"))
        conn.execute(text("INSERT INTO schema_migrations (version) VALUES (1)"))
        conn.execute(text("INSERT INTO api_keys (id, name, project, key_hash, key_prefix, created_at) "
                          "VALUES ('key_1', 'svc', 'acme', 'h', 'gw_live_', CURRENT_TIMESTAMP)"))
        conn.execute(text("INSERT INTO request_logs (id, created_at, endpoint, requested_model, status, http_status, "
                          "latency_ms, stream, fallback_used, cache_hit) "
                          "VALUES ('req_1', CURRENT_TIMESTAMP, 'chat', 'auto', 'success', 200, 5, false, false, false)"))


@pytest.mark.parametrize("kind", ["sqlite", "postgresql"])
def test_upgrade_from_v1_preserves_data_and_is_repeatable(kind, tmp_path, monkeypatch):
    with _engine(kind, tmp_path) as engine:
        monkeypatch.setattr(database, "engine", engine)
        _v1_schema_with_data(engine)

        with pytest.raises(RuntimeError, match="python -m app.ops migrate"):
            database.init_db(apply=False)  # MIGRATE_ON_STARTUP=false refuses an old schema
        assert database.init_db() == [2]
        assert database.init_db() == []
        database.init_db(apply=False)

        columns = lambda table: {c["name"] for c in inspect(engine).get_columns(table)}  # noqa: E731
        assert "user_id" in columns("api_keys") and "deployment" in columns("request_logs")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT project FROM api_keys WHERE id = 'key_1'")).scalar() == "acme"
            assert conn.execute(text("SELECT COUNT(*) FROM request_logs")).scalar() == 1
            assert database.schema_version(conn) == database.SCHEMA_VERSION


@pytest.mark.parametrize("kind", ["sqlite", "postgresql"])
def test_fresh_install_and_newer_schema_refusal(kind, tmp_path, monkeypatch):
    with _engine(kind, tmp_path) as engine:
        monkeypatch.setattr(database, "engine", engine)
        assert database.init_db() == [1, 2]
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO schema_migrations (version) VALUES (999)"))
        with pytest.raises(RuntimeError, match="newer than this gateway"):
            database.init_db()


def test_retention_and_verify(tmp_path, monkeypatch, capsys):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    monkeypatch.setattr(database, "engine", engine)
    database.init_db()
    old = datetime.now(timezone.utc) - timedelta(days=40)
    with engine.begin() as conn:
        for cid, when in (("conv_old", old), ("conv_new", datetime.now(timezone.utc))):
            conn.execute(text("INSERT INTO conversations (id, user_id, project, summary, version, created_at, updated_at) "
                              "VALUES (:id, 'u', 'p', '', 0, :t, :t)"), {"id": cid, "t": when.replace(tzinfo=None).isoformat(" ")})
            conn.execute(text("INSERT INTO messages (id, conversation_id, role, content, created_at) "
                              "VALUES (:mid, :id, 'user', 'x', :t)"), {"mid": "m" + cid, "id": cid, "t": when.replace(tzinfo=None).isoformat(" ")})

    assert ops.main(["retention", "--conversations-days", "30", "--dry-run"]) == 0
    assert "would delete 1" in capsys.readouterr().out
    assert ops.main(["retention", "--conversations-days", "30", "--request-logs-days", "30"]) == 0
    assert ops.main(["verify"]) == 0
    report = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert (report["conversations"], report["messages"], report["schema_version"]) == (1, 1, database.SCHEMA_VERSION)
    engine.dispose()


def test_outbox_reconcile_and_requeue(sqlite_engine, tmp_path, settings, monkeypatch, capsys):
    from sqlalchemy.orm import sessionmaker

    spool = tmp_path / "spool.db"
    monkeypatch.setattr(settings, "usage_spool_path", str(spool))
    monkeypatch.setattr(database, "engine", sqlite_engine)
    factory = sessionmaker(bind=sqlite_engine)
    row = dict(id="req_dup", created_at=datetime.now(timezone.utc), endpoint="chat", requested_model="auto",
               status="success", http_status=200, latency_ms=1, stream=False, fallback_used=False, cache_hit=False)
    writer = UsageWriter(factory, spool_path=str(spool), flush_interval_seconds=0.02)
    assert writer.submit(row) and writer.flush(timeout=5)
    writer.close()
    with sqlite3.connect(spool) as conn:  # one already-recorded and one genuinely missing deadletter
        for key in ("req_dup", "req_lost"):
            conn.execute("INSERT INTO deadletter (id, payload) VALUES (?, ?)",
                         (key, json.dumps({**row, "id": key, "created_at": row["created_at"].isoformat()})))

    assert ops.main(["outbox", "reconcile"]) == 0
    assert json.loads(capsys.readouterr().out) == {"already_recorded_removed": 1, "remaining": 1}
    assert ops.main(["outbox", "requeue"]) == 0
    writer = UsageWriter(factory, spool_path=str(spool), flush_interval_seconds=0.02)
    assert writer.flush(timeout=5)
    writer.close()
    with factory() as session:
        assert {r.id for r in session.query(models.RequestLog)} == {"req_dup", "req_lost"}
    assert ops.main(["outbox", "status"]) == 0
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1]) == {"pending": 0, "deadletter": 0}
