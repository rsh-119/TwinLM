"""Optional PostgreSQL dialect check in an isolated, disposable schema."""
import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.database import Base
from app.database import models  # noqa: F401
from app.database import repository
from app.gateway.usage import UsageWriter


def test_postgres_exchange_and_usage_replay(tmp_path):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    schema = "twinlm_test_" + uuid.uuid4().hex
    admin = create_engine(url)
    engine = None
    try:
        with admin.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {schema}"))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)
        with factory() as session:
            conversation = repository.create_conversation(session, "test-user")
            repository.add_exchange(session, conversation.id, "default", 0, "question", "answer")
            assert [message.content for message in repository.get_messages(session, conversation.id)] == [
                "question", "answer"
            ]
        spool = str(tmp_path / "usage.db")
        writer = UsageWriter(factory, spool_path=spool, flush_interval_seconds=0.02)
        assert writer.submit(dict(id="request-1", endpoint="chat", requested_model="auto",
                                  status="success", http_status=200, latency_ms=1,
                                  stream=False, fallback_used=False, cache_hit=False))
        assert writer.flush(timeout=5)
        writer.close()
        with factory() as session:
            assert session.query(models.RequestLog).count() == 1
    finally:
        if engine is not None:
            engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        admin.dispose()
