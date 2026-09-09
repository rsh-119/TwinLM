"""Plain CRUD operations against the database.

This module contains no business rules (no summarization thresholds, no
message-limit logic, no provider awareness) - that belongs in
`app/memory/conversation_manager.py`. Keeping the two separate means the
database layer can be tested and reasoned about on its own.
"""
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.database.models import Conversation, Memory, Message


def create_conversation(db: Session, user_id: str) -> Conversation:
    """Insert a new, empty conversation for a user."""
    conversation = Conversation(user_id=user_id, summary="")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def get_conversation(db: Session, conversation_id: str) -> Optional[Conversation]:
    """Fetch a conversation by id, or None if it doesn't exist."""
    return db.get(Conversation, conversation_id)


def update_conversation_summary(
    db: Session, conversation_id: str, summary: str
) -> Optional[Conversation]:
    """Overwrite a conversation's stored summary."""
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        return None
    conversation.summary = summary
    db.commit()
    db.refresh(conversation)
    return conversation


def add_message(
    db: Session,
    conversation_id: str,
    role: str,
    content: str,
    model: Optional[str] = None,
) -> Message:
    """Append a message to a conversation."""
    message = Message(conversation_id=conversation_id, role=role, content=content, model=model)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def get_messages(
    db: Session, conversation_id: str, limit: Optional[int] = None
) -> Sequence[Message]:
    """Return a conversation's messages in chronological order.

    If `limit` is given, only the most recent `limit` messages are returned
    (still in chronological order).
    """
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .all()
    )
    if limit is not None and len(messages) > limit:
        return messages[-limit:]
    return messages


def count_messages(db: Session, conversation_id: str) -> int:
    """Return the total number of messages stored for a conversation."""
    return db.query(Message).filter(Message.conversation_id == conversation_id).count()


def create_memory(db: Session, user_id: str, content: str) -> Memory:
    """Store a new long-term memory fact for a user."""
    memory = Memory(user_id=user_id, content=content)
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return memory


def get_memories(db: Session, user_id: str) -> Sequence[Memory]:
    """Return all stored memories for a user, oldest first."""
    return (
        db.query(Memory)
        .filter(Memory.user_id == user_id)
        .order_by(Memory.created_at.asc())
        .all()
    )
