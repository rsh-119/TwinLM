"""Tests for the conversation manager and context builder, against an
isolated in-memory database (db_session in conftest.py)."""
import pytest

from app.database import repository
from app.memory.context_builder import MAX_TEXT_CHARS, build_context
from app.memory.conversation_manager import (
    MAX_RECENT_MESSAGES,
    ConversationManager,
    ConversationNotFoundError,
)
from app.providers.base import TokenUsage


def test_create_and_get_conversation(db_session):
    manager = ConversationManager(db_session)

    conversation = manager.create_conversation("user_123")

    assert conversation.id.startswith("conv_")
    fetched = manager.get_conversation(conversation.id)
    assert fetched.id == conversation.id
    assert fetched.user_id == "user_123"


def test_get_conversation_raises_when_missing(db_session):
    manager = ConversationManager(db_session)

    with pytest.raises(ConversationNotFoundError):
        manager.get_conversation("conv_does_not_exist")


def test_save_message_raises_when_conversation_missing(db_session):
    manager = ConversationManager(db_session)

    with pytest.raises(ConversationNotFoundError):
        manager.save_message("conv_does_not_exist", "user", "hi")


def test_save_message_and_get_recent_messages(db_session):
    manager = ConversationManager(db_session)
    conversation = manager.create_conversation("user_123")

    manager.save_message(conversation.id, "user", "I am building a Kafka fraud detection system.")
    manager.save_message(conversation.id, "assistant", "Great, let's design it.", model="gemini")

    messages = manager.get_recent_messages(conversation.id)

    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[1].role == "assistant"
    assert messages[1].model == "gemini"


def test_get_recent_messages_respects_the_limit(db_session):
    manager = ConversationManager(db_session)
    conversation = manager.create_conversation("user_123")

    for i in range(15):
        manager.save_message(conversation.id, "user", f"message {i}")

    recent = manager.get_recent_messages(conversation.id)

    assert len(recent) == MAX_RECENT_MESSAGES
    assert recent[-1].content == "message 14"
    assert [m.content for m in recent] == [f"message {i}" for i in range(5, 15)]

    limited = manager.get_recent_messages(conversation.id, limit=3)
    assert len(limited) == 3
    assert limited[-1].content == "message 14"


def test_conversation_summary_defaults_empty_then_can_be_updated(db_session):
    manager = ConversationManager(db_session)
    conversation = manager.create_conversation("user_123")

    assert manager.get_conversation_summary(conversation.id) == ""

    manager.update_summary(conversation.id, "The user is building a Kafka system.")

    assert manager.get_conversation_summary(conversation.id) == "The user is building a Kafka system."


def test_delete_conversation_removes_it_and_its_messages(db_session):
    manager = ConversationManager(db_session)
    conversation = manager.create_conversation("user_123")
    manager.save_message(conversation.id, "user", "hi")

    manager.delete_conversation(conversation.id)

    with pytest.raises(ConversationNotFoundError):
        manager.get_conversation(conversation.id)
    assert repository.get_messages(db_session, conversation.id) == []


def test_delete_conversation_raises_when_missing(db_session):
    manager = ConversationManager(db_session)

    with pytest.raises(ConversationNotFoundError):
        manager.delete_conversation("conv_does_not_exist")


def test_save_message_persists_token_usage(db_session):
    manager = ConversationManager(db_session)
    conversation = manager.create_conversation("user_123")

    manager.save_message(
        conversation.id,
        "assistant",
        "Here you go.",
        model="gemini",
        usage=TokenUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120),
    )

    [message] = manager.get_all_messages(conversation.id)
    assert message.prompt_tokens == 100
    assert message.completion_tokens == 20
    assert message.total_tokens == 120


def test_save_message_without_usage_leaves_token_fields_null(db_session):
    manager = ConversationManager(db_session)
    conversation = manager.create_conversation("user_123")

    manager.save_message(conversation.id, "user", "hi")

    [message] = manager.get_all_messages(conversation.id)
    assert message.total_tokens is None


def test_total_tokens_sums_across_turns_and_ignores_unrecorded_ones(db_session):
    manager = ConversationManager(db_session)
    conversation = manager.create_conversation("user_123")

    manager.save_message(conversation.id, "user", "hi")
    manager.save_message(
        conversation.id, "assistant", "hey", model="gemini",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    manager.save_message(conversation.id, "user", "and?")
    manager.save_message(
        conversation.id, "assistant", "and that", model="groq",
        usage=TokenUsage(prompt_tokens=30, completion_tokens=8, total_tokens=38),
    )

    assert manager.get_total_tokens(conversation.id) == 53


def test_total_tokens_is_zero_for_a_conversation_with_no_usage_recorded(db_session):
    manager = ConversationManager(db_session)
    conversation = manager.create_conversation("user_123")
    manager.save_message(conversation.id, "user", "hi")

    assert manager.get_total_tokens(conversation.id) == 0


def test_message_count_and_get_all_messages(db_session):
    manager = ConversationManager(db_session)
    conversation = manager.create_conversation("user_123")

    for i in range(3):
        manager.save_message(conversation.id, "user", f"message {i}")

    assert manager.get_message_count(conversation.id) == 3
    assert len(manager.get_all_messages(conversation.id)) == 3


def test_create_and_retrieve_memories(db_session):
    repository.create_memory(db_session, "user_123", "Prefers Python examples.")
    repository.create_memory(db_session, "user_123", "Works with Kafka a lot.")
    repository.create_memory(db_session, "user_456", "Prefers JavaScript.")

    memories = repository.get_memories(db_session, "user_123")

    assert [m.content for m in memories] == [
        "Prefers Python examples.",
        "Works with Kafka a lot.",
    ]
    other_user_memories = repository.get_memories(db_session, "user_456")
    assert len(other_user_memories) == 1


def test_build_context_with_no_history_yet_is_just_the_user_message():
    result = build_context(summary="", memories=[], recent_messages=[], current_message="Hi there")

    assert result == [{"role": "user", "content": "Hi there"}]


def test_build_context_combines_summary_memory_and_history():
    summary = "The user is building a Kafka based fraud detection system."
    memories = ["The user prefers Python examples."]
    recent_messages = [
        {"role": "user", "content": "How does Kafka work?"},
        {"role": "assistant", "content": "Kafka is a distributed event streaming platform."},
    ]

    result = build_context(summary, memories, recent_messages, "How should I implement retries?")

    assert result[0]["role"] == "system"
    assert "Conversation summary:" in result[0]["content"]
    assert summary in result[0]["content"]
    assert "User memory:" in result[0]["content"]
    assert memories[0] in result[0]["content"]
    assert "Recent conversation:" in result[0]["content"]
    assert "How does Kafka work?" in result[0]["content"]
    assert result[1] == {"role": "user", "content": "How should I implement retries?"}


def test_build_context_truncates_an_oversized_recent_message():
    """Regression: a single huge pasted message pushed a real request past
    Groq's tokens-per-minute quota (a 413, not a normal 429) - one message
    that big shouldn't be able to do that."""
    huge_message = "x" * (MAX_TEXT_CHARS * 3)
    recent_messages = [{"role": "user", "content": huge_message}]

    result = build_context("", [], recent_messages, "a normal follow-up")

    system_content = result[0]["content"]
    assert len(system_content) < len(huge_message)
    assert "truncated" in system_content


def test_build_context_truncates_oversized_summary_memory_and_current_message():
    huge = "y" * (MAX_TEXT_CHARS * 2)

    result = build_context(huge, [huge], [], huge)

    system_content = result[0]["content"]
    assert huge not in system_content
    assert "truncated" in system_content
    assert huge not in result[1]["content"]
    assert "truncated" in result[1]["content"]


def test_build_context_leaves_normal_sized_text_untouched():
    result = build_context(
        "short summary",
        ["short memory"],
        [{"role": "user", "content": "short message"}],
        "short current message",
    )

    assert "truncated" not in result[0]["content"]
    assert "truncated" not in result[1]["content"]


def test_build_context_bounds_total_memory_text():
    from app.memory.context_builder import MAX_MEMORY_CHARS

    memories = ["old-fact-" + "x" * 3900 for _ in range(100)]
    result = build_context("", memories, [], "question")
    assert len(result[0]["content"]) <= MAX_MEMORY_CHARS + 100


def test_memory_pages_are_bounded_and_cursor_ordered(db_session):
    from app.database import repository

    for index in range(5):
        repository.create_memory(db_session, "u", f"fact-{index}")
    newest = repository.get_memories(db_session, "u", limit=2)
    previous = repository.get_memories(db_session, "u", limit=2, before_id=newest[0].id)
    assert [memory.content for memory in newest] == ["fact-3", "fact-4"]
    assert [memory.content for memory in previous] == ["fact-1", "fact-2"]


def test_exchange_is_atomic_and_rejects_stale_context(db_session):
    import pytest
    from sqlalchemy.exc import IntegrityError
    from app.database import repository

    conversation = repository.create_conversation(db_session, "u")
    with pytest.raises(IntegrityError):
        repository.add_exchange(db_session, conversation.id, "default", 0, "user", None)
    assert repository.get_messages(db_session, conversation.id) == []
    assert repository.get_conversation(db_session, conversation.id).version == 0

    repository.add_exchange(db_session, conversation.id, "default", 0, "user", "assistant")
    with pytest.raises(repository.ConversationConflictError):
        repository.add_exchange(db_session, conversation.id, "default", 0, "stale", "reply")
    assert [m.content for m in repository.get_messages(db_session, conversation.id)] == ["user", "assistant"]
    assert repository.get_conversation(db_session, conversation.id).version == 1


def test_startup_upgrades_old_conversation_table_idempotently(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect, text
    from app.database import database

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE conversations (id VARCHAR PRIMARY KEY, user_id VARCHAR NOT NULL, "
            "summary TEXT NOT NULL, created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL)"
        ))
    monkeypatch.setattr(database, "engine", engine)
    database.init_db()
    database.init_db()
    with engine.connect() as conn:
        columns = {column["name"] for column in inspect(conn).get_columns("conversations")}
        assert {"project", "version"}.issubset(columns)
        assert conn.execute(text("SELECT MAX(version) FROM schema_migrations")).scalar() == database.SCHEMA_VERSION
        assert "ix_messages_conversation_created" in {
            index["name"] for index in inspect(conn).get_indexes("messages")
        }
    engine.dispose()
