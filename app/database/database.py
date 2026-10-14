"""SQLAlchemy engine, session factory, and versioned schema migrations.

Migrations are forward-only and idempotent (add-if-missing), recorded in
`schema_migrations`, and applied under a database lock. Startup applies
them unless MIGRATE_ON_STARTUP=false, in which case it refuses to run on
an outdated schema (`python -m app.ops migrate` applies them). A database
newer than this code is refused. Rollback = restore the pre-upgrade backup
(docs/OPERATIONS.md); there are no down-migrations.
"""
from pathlib import Path
from typing import Callable, Iterator, List, Tuple

from sqlalchemy import create_engine, event, inspect, make_url, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import get_settings

settings = get_settings()


def _database_url():
    url = make_url(settings.database_url)
    if settings.database_password_file and url.password is None:
        url = url.set(password=Path(settings.database_password_file).read_text(encoding="utf-8").strip())
    return url


# SQLite requires check_same_thread=False to allow multiple threads to use the same connection.
_connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

if settings.database_url.startswith("sqlite"):
    engine = create_engine(_database_url(), connect_args=_connect_args)
else:
    # pre-ping replaces connections killed by a database restart; the
    # connect timeout bounds how long a request waits on a dead database
    engine = create_engine(
        _database_url(),
        pool_size=settings.db_pool_size,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_timeout=5,
        connect_args={"connect_timeout": 3},
    )
if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def schema_version(conn: Connection) -> int:
    if "schema_migrations" not in inspect(conn).get_table_names():
        return 0
    return conn.execute(text("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")).scalar()


def init_db(apply: bool = True) -> List[int]:
    """Bring the schema to SCHEMA_VERSION; returns the versions applied.
    With `apply=False`, only verifies the schema is current."""
    from app.database import models  # noqa: F401  (registers models with Base)

    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(1414942285)"))
        elif conn.dialect.name == "sqlite":
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        current = schema_version(conn)
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema version {current} is newer than this gateway supports ({SCHEMA_VERSION}); "
                "deploy the matching release or restore a backup"
            )
        pending = [(version, migrate) for version, migrate in MIGRATIONS if version > current]
        if pending and not apply:
            raise RuntimeError(
                f"database schema is at version {current}, this gateway needs {SCHEMA_VERSION}: "
                "back up, then run `python -m app.ops migrate`"
            )
        if not apply:
            return []
        Base.metadata.create_all(bind=conn)
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
        for version, migrate in pending:
            migrate(conn)
            conn.execute(text("INSERT INTO schema_migrations (version) VALUES (:v)"), {"v": version})
        return [version for version, _ in pending]


# Columns added to a model after its table already existed on disk -
# `create_all` only creates missing tables. This additive migration upgrades
# older schemas in place; changes that rename/drop/retype need a versioned
# offline migration and backup before deployment.
_PATCHED_COLUMNS = {
    "conversations": {
        "project": "VARCHAR NOT NULL DEFAULT 'default'",
        "version": "INTEGER NOT NULL DEFAULT 0",
    },
    "memories": {"project": "VARCHAR NOT NULL DEFAULT 'default'"},
    "messages": {
        "prompt_tokens": "INTEGER",
        "completion_tokens": "INTEGER",
        "total_tokens": "INTEGER",
    },
}


def _add_columns(conn: Connection, patches: dict) -> None:
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    for table, columns in patches.items():
        if table not in existing_tables:
            continue
        existing_columns = {col["name"] for col in inspector.get_columns(table)}
        for column, sql_type in columns.items():
            if column not in existing_columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))


def _add_missing_columns(conn: Connection | None = None) -> None:
    """Migration 1: columns and indexes added before versioning existed."""
    if conn is None:
        with engine.begin() as connection:
            _add_missing_columns(connection)
        return
    _add_columns(conn, _PATCHED_COLUMNS)
    existing_tables = set(inspect(conn).get_table_names())
    if "messages" in existing_tables:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_messages_conversation_created "
            "ON messages (conversation_id, created_at)"
        ))
    if "request_logs" in existing_tables:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_request_logs_key_created "
            "ON request_logs (api_key_id, created_at)"
        ))


def _user_keys_and_deployments(conn: Connection) -> None:
    """Migration 2: user-bound API keys; deployment id on request logs."""
    _add_columns(conn, {"api_keys": {"user_id": "VARCHAR"}, "request_logs": {"deployment": "VARCHAR"}})


# Append only. Each migration must be idempotent: it also runs after
# create_all() has built a fresh schema.
MIGRATIONS: List[Tuple[int, Callable[[Connection], None]]] = [
    (1, _add_missing_columns),
    (2, _user_keys_and_deployments),
]
SCHEMA_VERSION = MIGRATIONS[-1][0]


def release_connection(db: Session) -> None:
    """Return the session's pooled connection before awaiting slow I/O (a
    provider call). Async handlers run their DB calls on the event loop, so
    holding connections across awaits exhausts the pool under concurrency,
    and the next checkout then blocks the loop itself. The session stays
    usable afterwards, and already-loaded objects keep their attribute
    values. Only call it with no uncommitted changes pending."""
    db.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
