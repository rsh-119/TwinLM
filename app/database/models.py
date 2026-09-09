"""SQLAlchemy ORM models: conversations, messages, and user memories.

The conversation is owned by the gateway, not by any LLM provider - a
`Conversation` has many `Message` rows regardless of which provider produced
each one. See `app/memory/` for the logic that uses these tables.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import relationship

from app.database.database import Base


def _new_id(prefix: str) -> str:
    """Generate a short, prefixed unique id, e.g. 'conv_a1b2c3d4e5f6'."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Conversation(Base):
    """A conversation thread. Independent of any specific LLM provider."""

    __tablename__ = "conversations"

    id = Column(String, primary_key=True, default=lambda: _new_id("conv"))
    user_id = Column(String, nullable=False, index=True)
    summary = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    """A single user or assistant message within a conversation."""

    __tablename__ = "messages"

    id = Column(String, primary_key=True, default=lambda: _new_id("msg"))
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    role = Column(String, nullable=False)  # "user", "assistant", or "system"
    content = Column(Text, nullable=False)
    model = Column(String, nullable=True)  # which provider/model produced this message, if any
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    conversation = relationship("Conversation", back_populates="messages")


class Memory(Base):
    """A simple long-term fact about a user, reused across all their conversations."""

    __tablename__ = "memories"

    id = Column(String, primary_key=True, default=lambda: _new_id("mem"))
    user_id = Column(String, nullable=False, index=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
