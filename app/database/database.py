"""SQLAlchemy engine, session factory, and table setup.

This module owns the raw database plumbing only. Nothing here knows about
conversations, providers, or routing - it just gives the rest of the app a
`Session` to work with.
"""
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import get_settings

settings = get_settings()

# SQLite needs check_same_thread=False because FastAPI may use a different
# thread per request than the one that created the connection.
_connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(settings.database_url, connect_args=_connect_args)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def init_db() -> None:
    """Create all tables that don't already exist.

    Safe to call on every startup - existing tables are left untouched.
    """
    from app.database import models  # noqa: F401  (registers models with Base)

    Base.metadata.create_all(bind=engine)


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a request-scoped database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
