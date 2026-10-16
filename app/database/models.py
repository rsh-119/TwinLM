"""SQLAlchemy ORM models: conversations, messages, user memories, API keys,
request logs, and the providers/aliases/prices added from the admin panel."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    # console user who created it (null: admin token or older keys)
    created_by = Column(String, nullable=True)


class ProviderConfigRow(Base):
    """A provider added from the admin panel (gateway.toml providers are not
    stored here). The API key is Fernet-encrypted; only `key_hint` (its last
    characters) is ever shown."""

    __tablename__ = "provider_configs"

    name = Column(String, primary_key=True)
    preset = Column(String, nullable=True)
    type = Column(String, nullable=False)
    base_url = Column(String, nullable=True)
    default_model = Column(String, nullable=False)
    capabilities = Column(Text, nullable=True)  # JSON list; null = adapter default
    timeout_seconds = Column(Float, nullable=False, default=30.0)
    enabled = Column(Boolean, nullable=False, default=True)
    api_key_encrypted = Column(Text, nullable=True)
    key_hint = Column(String, nullable=True)
    # "authorization" (Bearer, default) or "api-key" (Azure OpenAI)
    auth_header = Column(String, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class AliasOverride(Base):
    """A model alias set from the admin panel; replaces a gateway.toml alias
    of the same name."""

    __tablename__ = "alias_overrides"

    alias = Column(String, primary_key=True)
    targets = Column(Text, nullable=False)  # JSON list, in fallback order
    max_output_tokens = Column(Integer, nullable=True)
    cache_ttl_seconds = Column(Float, nullable=True)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class PriceOverride(Base):
    """USD per million tokens for "provider/model", set from the admin panel;
    overrides gateway.toml [pricing]."""

    __tablename__ = "price_overrides"

    key = Column(String, primary_key=True)
    input_per_million = Column(Float, nullable=False)
    output_per_million = Column(Float, nullable=False)


class User(Base):
    """A person who signs in to the console. Org role `admin` manages
    everything; `member` sees only their teams."""

    __tablename__ = "users"

    id = Column(String, primary_key=True, default=lambda: _new_id("usr"))
    email = Column(String, nullable=False, unique=True, index=True)  # lowercased
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="member")
    must_change_password = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    last_login_at = Column(DateTime, nullable=True)
    disabled_at = Column(DateTime, nullable=True)


class Team(Base):
    """A team. Its name is the `project` of the team's API keys, so usage,
    savings, cache and conversation isolation per project are per team."""

    __tablename__ = "teams"

    id = Column(String, primary_key=True, default=lambda: _new_id("team"))
    name = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)


class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_team_member"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(String, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String, nullable=False, default="member")  # admin | member
    added_at = Column(DateTime, default=_utcnow, nullable=False)


class UserSession(Base):
    """A console sign-in. Only the SHA-256 of the session token is stored."""

    __tablename__ = "user_sessions"

    token_hash = Column(String(64), primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)


class Budget(Base):
    """A spending limit in USD per period. Scope `org` (target "*") covers
    everything, `team` a project/team name, `user` a user id (their
    personal keys and playground use). API keys keep their own
    `daily_budget_usd`."""

    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("scope", "target", "period", name="uq_budget"),)

    id = Column(String, primary_key=True, default=lambda: _new_id("bud"))
    scope = Column(String, nullable=False)  # org | team | user
    target = Column(String, nullable=False)
    period = Column(String, nullable=False)  # daily | monthly (UTC)
    limit_usd = Column(Float, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)


class BudgetAlert(Base):
    """A budget crossed a threshold (percent) in a period; the unique
    constraint makes each alert fire once, across instances too."""

    __tablename__ = "budget_alerts"
    __table_args__ = (UniqueConstraint("budget_id", "period_start", "threshold", name="uq_budget_alert"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    budget_id = Column(String, ForeignKey("budgets.id", ondelete="CASCADE"), nullable=False, index=True)
    period_start = Column(DateTime, nullable=False)
    threshold = Column(Integer, nullable=False)
    spent_usd = Column(Float, nullable=False)
    limit_usd = Column(Float, nullable=False)
    created_at = Column(DateTime, default=_utcnow, nullable=False)


class RequestLog(Base):
    """One row per gateway LLM request - the source for usage, cost and
    per-key budget accounting. Prompts and replies are never stored here."""

    __tablename__ = "request_logs"

    id = Column(String, primary_key=True)  # the request ID
    created_at = Column(DateTime, default=_utcnow, nullable=False, index=True)
    endpoint = Column(String, nullable=False)  # "chat" | "embeddings"
    api_key_id = Column(String, nullable=True, index=True)
    project = Column(String, nullable=True, index=True)
    user_id = Column(String, nullable=True, index=True)  # the key's user (user budgets)
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
