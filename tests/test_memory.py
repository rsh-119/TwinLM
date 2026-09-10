"""Tests for conversation memory: the conversation manager and context builder.

Uses an isolated in-memory SQLite database (see `db_session` in
conftest.py) - never the real configured database.
"""
import pytest

from app.database import repository
from app.memory.context_builder import build_context
from app.memory.conversation_manager import (
    MAX_RECENT_MESSAGES,
    ConversationManager,
    ConversationNotFoundError,
)


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
    # oldest-first ordering is preserved within the limited window
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
    # memories are isolated per user
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
