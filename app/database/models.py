"""SQLAlchemy ORM models: conversations, messages, user memories, API keys
and request logs."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
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
    # the owning API key's project; other projects can't see this row
    project = Column(String, nullable=False, default="default")
    summary = Column(Text, nullable=False, default="")
    version = Column(Integer, nullable=False, default=0)
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
    # Token usage for this turn, as reported by the provider - only ever set
    # on assistant messages, and only when the provider's response reported
    # it. Null (not 0) means "unknown", not "free".
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)

    conversation = relationship("Conversation", back_populates="messages")


class Memory(Base):
    """A simple long-term fact about a user, reused across all their conversations."""

    __tablename__ = "memories"

    id = Column(String, primary_key=True, default=lambda: _new_id("mem"))
    user_id = Column(String, nullable=False, index=True)
    project = Column(String, nullable=False, default="default")
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)


class ApiKey(Base):
    """A gateway-issued API key. Only the SHA-256 of the key is stored; the
    raw key is shown once, at creation."""

    __tablename__ = "api_keys"

    id = Column(String, primary_key=True, default=lambda: _new_id("key"))
    name = Column(String, nullable=False)
    project = Column(String, nullable=False, default="default", index=True)
    key_hash = Column(String(64), nullable=False, unique=True, index=True)
    key_prefix = Column(String, nullable=False)  # first chars, for display
    # JSON list of allowed `model` values; null = any
    allowed_models = Column(Text, nullable=True)
    rpm_limit = Column(Integer, nullable=True)
    tpm_limit = Column(Integer, nullable=True)
    daily_budget_usd = Column(Float, nullable=True)
    # set = user-bound key: may only touch this user's conversations/memories
    user_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    revoked_at = Column(DateTime, nullable=True)


class RequestLog(Base):
    """One row per gateway LLM request - the source for usage, cost and
    per-key budget accounting. Prompts and replies are never stored here."""

    __tablename__ = "request_logs"

    id = Column(String, primary_key=True)  # the request ID
    created_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
    endpoint = Column(String, nullable=False)  # "chat" | "embeddings"
    api_key_id = Column(String, nullable=True, index=True)
    project = Column(String, nullable=True, index=True)
    conversation_id = Column(String, nullable=True)
    requested_model = Column(String, nullable=False)
    provider = Column(String, nullable=True)
    model = Column(String, nullable=True)
    deployment = Column(String, nullable=True)  # deployment id that served it
    stream = Column(Boolean, nullable=False, default=False)
    status = Column(String, nullable=False)  # success | error | client_disconnected | blocked | rate_limited
    http_status = Column(Integer, nullable=False)
    error = Column(Text, nullable=True)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    cost_usd = Column(Float, nullable=True)
    latency_ms = Column(Integer, nullable=False)
    ttft_ms = Column(Integer, nullable=True)
    fallback_used = Column(Boolean, nullable=False, default=False)
    cache_hit = Column(Boolean, nullable=False, default=False)
