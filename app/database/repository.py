"""Plain CRUD operations against the database - no business rules live here."""
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from sqlalchemy import and_, case, func, or_, update
from sqlalchemy.orm import Session

from app.database.models import ApiKey, Conversation, Memory, Message, RequestLog


def create_conversation(db: Session, user_id: str, project: str = "default") -> Conversation:
    """Insert a new, empty conversation for a user."""
    conversation = Conversation(user_id=user_id, project=project, summary="")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def get_conversation(db: Session, conversation_id: str) -> Optional[Conversation]:
    """Fetch a conversation by id, or None if it doesn't exist."""
    return db.get(Conversation, conversation_id)


def get_conversations_by_user(db: Session, user_id: str, project: str = "default") -> Sequence[Conversation]:
    """Return a user's conversations, most recently created first."""
    return (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id, Conversation.project == project)
        .order_by(Conversation.created_at.desc())
        .limit(100)
        .all()
    )


def delete_conversation(db: Session, conversation_id: str) -> bool:
    """Delete a conversation and its messages. Returns False if it didn't exist."""
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        return False
    db.delete(conversation)
    db.commit()
    return True


def update_conversation_summary(
    db: Session, conversation_id: str, summary: str, expected_version: Optional[int] = None
) -> Optional[Conversation]:
    """Overwrite a conversation's stored summary."""
    if expected_version is not None:
        changed = db.execute(
            update(Conversation)
            .where(Conversation.id == conversation_id, Conversation.version == expected_version)
            .values(summary=summary)
        )
        if changed.rowcount != 1:
            db.rollback()
            raise ConversationConflictError(conversation_id)
        db.commit()
        return db.get(Conversation, conversation_id)
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
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
) -> Message:
    """Append a message to a conversation."""
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


class ConversationConflictError(Exception):
    """The conversation changed after a turn read its context."""


def add_exchange(
    db: Session, conversation_id: str, project: str, expected_version: int,
    user_content: str, assistant_content: str, model: Optional[str] = None,
    prompt_tokens: Optional[int] = None, completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
) -> None:
    """Atomically append an exchange if the context version is current."""
    try:
        changed = db.execute(
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.project == project,
                Conversation.version == expected_version,
            )
            .values(version=Conversation.version + 1)
        )
        if changed.rowcount != 1:
            raise ConversationConflictError(conversation_id)
        db.add_all([
            Message(conversation_id=conversation_id, role="user", content=user_content),
            Message(
                conversation_id=conversation_id, role="assistant", content=assistant_content,
                model=model, prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens, total_tokens=total_tokens,
            ),
        ])
        db.commit()
    except BaseException:
        db.rollback()
        raise


def get_total_tokens(db: Session, conversation_id: str) -> int:
    """Sum of `total_tokens` across a conversation's messages - the ones
    with no usage recorded (predate this feature, or the provider that
    answered didn't report it) just don't contribute. This is billed
    tokens, not unique ones: since each turn resends the growing context,
    it deliberately double-counts earlier messages the same way the
    provider itself billed for them."""
    total = (
        db.query(func.sum(Message.total_tokens))
        .filter(Message.conversation_id == conversation_id)
        .scalar()
    )
    return total or 0


def get_messages(
    db: Session, conversation_id: str, limit: Optional[int] = None, before_id: Optional[str] = None
) -> Sequence[Message]:
    """Return a conversation's messages in chronological order, optionally
    limited to the most recent `limit` of them."""
    query = db.query(Message).filter(Message.conversation_id == conversation_id)
    if before_id is not None:
        before = db.get(Message, before_id)
        if before is None or before.conversation_id != conversation_id:
            return []
        query = query.filter(or_(
            Message.created_at < before.created_at,
            and_(Message.created_at == before.created_at, Message.id < before.id),
        ))
    if limit is not None:
        if limit <= 0:
            return []
        return list(reversed(query.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit).all()))
    return query.order_by(Message.created_at.asc(), Message.id.asc()).all()


def get_first_message(db: Session, conversation_id: str) -> Optional[Message]:
    """Return a conversation's earliest message, if any - used to label it
    in the UI sidebar without loading the whole conversation."""
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .first()
    )


def count_messages(db: Session, conversation_id: str) -> int:
    """Return the total number of messages stored for a conversation."""
    return db.query(Message).filter(Message.conversation_id == conversation_id).count()


def create_memory(db: Session, user_id: str, content: str, project: str = "default") -> Memory:
    """Store a new long-term memory fact for a user."""
    memory = Memory(user_id=user_id, project=project, content=content)
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return memory


def get_memories(
    db: Session, user_id: str, project: str = "default", limit: int = 100,
    before_id: Optional[str] = None,
) -> Sequence[Memory]:
    """Return a bounded page of recent facts, oldest first within the page."""
    if limit <= 0:
        return []
    query = db.query(Memory).filter(Memory.user_id == user_id, Memory.project == project)
    if before_id is not None:
        before = db.get(Memory, before_id)
        if before is None or before.user_id != user_id or before.project != project:
            return []
        query = query.filter(or_(
            Memory.created_at < before.created_at,
            and_(Memory.created_at == before.created_at, Memory.id < before.id),
        ))
    return list(reversed(query.order_by(Memory.created_at.desc(), Memory.id.desc()).limit(limit).all()))


# ---- API keys ----------------------------------------------------------------


def create_api_key(db: Session, **fields) -> ApiKey:
    key = ApiKey(**fields)
    db.add(key)
    db.commit()
    db.refresh(key)
    return key


def get_api_key(db: Session, key_id: str) -> Optional[ApiKey]:
    return db.get(ApiKey, key_id)


def get_api_key_by_hash(db: Session, key_hash: str) -> Optional[ApiKey]:
    return db.query(ApiKey).filter(ApiKey.key_hash == key_hash).first()


def list_api_keys(db: Session) -> Sequence[ApiKey]:
    return db.query(ApiKey).order_by(ApiKey.created_at.asc()).all()


def revoke_api_key(db: Session, key_id: str) -> Optional[ApiKey]:
    key = db.get(ApiKey, key_id)
    if key is None:
        return None
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(key)
    return key


# ---- request logs ------------------------------------------------------------


def add_request_log(db: Session, **fields) -> RequestLog:
    log = RequestLog(**fields)
    db.add(log)
    db.commit()
    return log


def sum_cost_since(
    db: Session, since: datetime, api_key_id: Optional[str] = None,
    project: Optional[str] = None, user_id: Optional[str] = None,
) -> float:
    """Known cost since `since`; no filter = the whole organization."""
    filters = {RequestLog.api_key_id: api_key_id, RequestLog.project: project, RequestLog.user_id: user_id}
    total = (
        db.query(func.sum(RequestLog.cost_usd))
        .filter(RequestLog.created_at >= since, *[col == value for col, value in filters.items() if value is not None])
        .scalar()
    )
    return total or 0.0


def usage_summary(
    db: Session, since: datetime, group_by: str,
    project: Optional[str] = None, api_key_ids: Optional[Sequence[str]] = None,
) -> List[dict]:
    """Aggregates over request_logs since `since`, grouped by one of
    provider | model | deployment | project | api_key_id. Grouping by "model"
    groups by provider+model, since the same model name can exist on two
    providers.

    Token sums count provider consumption only, so cache hits (no provider
    call) are excluded. `cost_usd` sums *known* costs; `unpriced_requests`
    counts rows that reached a provider but whose cost is unknown (no price
    or no usage reported) - when it is non-zero the cost is incomplete.
    `project` / `api_key_ids` narrow it to one team / some keys."""
    columns = {
        "provider": [RequestLog.provider],
        "model": [RequestLog.provider, RequestLog.model],
        "deployment": [RequestLog.deployment],
        "project": [RequestLog.project],
        "api_key_id": [RequestLog.api_key_id],
    }[group_by]
    provider_tokens = lambda column: func.sum(case((RequestLog.cache_hit.is_(True), 0), else_=column))  # noqa: E731
    rows = (
        db.query(
            *columns,
            func.count(RequestLog.id),
            func.sum(case((RequestLog.status != "success", 1), else_=0)),
            func.sum(case((RequestLog.fallback_used.is_(True), 1), else_=0)),
            func.sum(case((RequestLog.cache_hit.is_(True), 1), else_=0)),
            provider_tokens(RequestLog.prompt_tokens),
            provider_tokens(RequestLog.completion_tokens),
            provider_tokens(RequestLog.total_tokens),
            func.sum(RequestLog.cost_usd),
            func.sum(case((and_(RequestLog.cost_usd.is_(None), RequestLog.provider.isnot(None)), 1), else_=0)),
            func.avg(RequestLog.latency_ms),
            func.avg(RequestLog.ttft_ms),
        )
        .filter(RequestLog.created_at >= since)
        .filter(*([RequestLog.project == project] if project is not None else []))
        .filter(*([RequestLog.api_key_id.in_(list(api_key_ids))] if api_key_ids is not None else []))
        .group_by(*columns)
        .all()
    )
    keys = [c.key for c in columns]
    out = []
    for row in rows:
        group = dict(zip(keys, row[: len(keys)]))
        requests, errors, fallbacks, cache_hits, prompt, completion, total, cost, unpriced, latency, ttft = row[len(keys):]
        out.append(
            {
                **group,
                "requests": requests,
                "errors": errors or 0,
                "fallbacks": fallbacks or 0,
                "cache_hits": cache_hits or 0,
                "prompt_tokens": prompt or 0,
                "completion_tokens": completion or 0,
                "total_tokens": total or 0,
                "cost_usd": cost,
                "unpriced_requests": unpriced or 0,
                "avg_latency_ms": round(latency, 1) if latency is not None else None,
                "avg_ttft_ms": round(ttft, 1) if ttft is not None else None,
            }
        )
    return out


def daily_usage(
    db: Session, since: datetime, project: Optional[str] = None, api_key_ids: Optional[Sequence[str]] = None,
) -> List[dict]:
    """Requests, known cost and cache hits per UTC day, oldest first."""
    day = func.date(RequestLog.created_at)
    rows = (
        db.query(day, func.count(RequestLog.id), func.sum(RequestLog.cost_usd),
                 func.sum(case((RequestLog.cache_hit.is_(True), 1), else_=0)))
        .filter(RequestLog.created_at >= since)
        .filter(*([RequestLog.project == project] if project is not None else []))
        .filter(*([RequestLog.api_key_id.in_(list(api_key_ids))] if api_key_ids is not None else []))
        .group_by(day).order_by(day).all()
    )
    return [{"date": str(d), "requests": n, "cost_usd": round(c or 0.0, 8), "cache_hits": h or 0}
            for d, n, c, h in rows]


def savings_rows(db: Session, since: datetime, group_by: str) -> List[tuple]:
    """Per (group, provider, model, cache_hit): requests, measured requests,
    and the measured rows' prompt tokens, completion tokens and paid cost.
    A row is *measured* when its tokens are known and, unless it was a
    cache hit, its cost is known. Only rows that reached a provider (or the
    cache) count."""
    column = {
        "project": RequestLog.project,
        "api_key_id": RequestLog.api_key_id,
        "provider": RequestLog.provider,
        "deployment": RequestLog.deployment,
    }[group_by]
    measured = and_(
        RequestLog.total_tokens.isnot(None),
        or_(RequestLog.cache_hit.is_(True), RequestLog.cost_usd.isnot(None)),
    )
    return (
        db.query(
            column,
            RequestLog.provider,
            RequestLog.model,
            RequestLog.cache_hit,
            func.count(RequestLog.id),
            func.sum(case((measured, 1), else_=0)),
            func.sum(case((measured, RequestLog.prompt_tokens), else_=0)),
            func.sum(case((measured, RequestLog.completion_tokens), else_=0)),
            func.sum(case((and_(measured, RequestLog.cache_hit.is_(False)), RequestLog.cost_usd), else_=0)),
        )
        .filter(RequestLog.created_at >= since, RequestLog.provider.isnot(None))
        .group_by(column, RequestLog.provider, RequestLog.model, RequestLog.cache_hit)
        .all()
    )


def latencies_since(db: Session, since: datetime, provider: str, model: Optional[str] = None) -> List[int]:
    query = db.query(RequestLog.latency_ms).filter(
        RequestLog.created_at >= since, RequestLog.provider == provider
    )
    if model is not None:
        query = query.filter(RequestLog.model == model)
    return [
        row[0]
        for row in query.all()
    ]
