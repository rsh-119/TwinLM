"""Optional stateful layer above stateless proxying: conversation context,
memories, rolling summaries, and who may touch them.

`app/api/chat.py` calls in here only when a request carries
`conversation_id`; stateless requests never load or store conversation
data. STATEFUL_ENABLED=false turns this layer off (400 on
`conversation_id`; the conversation/memory routers are not mounted).

Ownership: data is scoped to the key's project. A *user-bound* key
(`api_keys.user_id`) can only see its own user's conversations and
memories; a *service* key (no user) may act for any `user_id` in its
project unless STATEFUL_REQUIRES_USER_KEYS=true.
"""
import logging
import uuid
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.common import GatewayServices, admit, provider_errors
from app.auth import Principal, project_of
from app.config import get_settings
from app.database import repository
from app.database.database import release_connection
from app.database.models import Conversation
from app.gateway.guardrails import GuardrailViolation
from app.gateway.usage import UsageRecord, finish
from app.memory.context_builder import build_context
from app.memory.conversation_manager import ConversationManager, ConversationNotFoundError
from app.memory.summarizer import should_summarize, summarize_conversation
from app.router.model_router import ModelRouter
from app.schemas.chat import ChatCompletionRequest

logger = logging.getLogger("twinlm.api.stateful")


def bound_user(principal: Optional[Principal]) -> Optional[str]:
    """The user a key is bound to, or None for service keys / no auth.
    Enforces STATEFUL_REQUIRES_USER_KEYS."""
    if principal is None:
        return None
    if principal.user_id is None and get_settings().stateful_requires_user_keys:
        raise HTTPException(status_code=403, detail="conversations and memories require a user-bound API key")
    return principal.user_id


def owner_for(principal: Optional[Principal], requested: Optional[str]) -> str:
    """The `user_id` to act for: the key's own user, or what a service key asked for."""
    bound = bound_user(principal)
    if bound is None:
        if not requested:
            raise HTTPException(status_code=400, detail="user_id is required for service keys")
        return requested
    if requested and requested != bound:
        raise HTTPException(status_code=403, detail="this API key is bound to a different user")
    return bound


def manager_for(db: Session, principal: Optional[Principal]) -> ConversationManager:
    return ConversationManager(db, project_of(principal), bound_user(principal))


def prepare_turn(
    body: ChatCompletionRequest,
    db: Session,
    principal: Optional[Principal],
    services: GatewayServices,
    record: UsageRecord,
) -> Tuple[ConversationManager, Conversation, str, List[dict]]:
    """(manager, conversation, guarded user message, provider context).
    Guardrails run on the new message - redactions also apply to what gets
    stored - and again on the assembled context (history, memories)."""
    if not get_settings().stateful_enabled:
        raise HTTPException(status_code=400, detail="conversation_id is not supported: stateful mode is disabled")
    manager = manager_for(db, principal)
    try:
        conversation = manager.get_conversation(body.conversation_id)
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="conversation not found")

    user_message = body.latest_user_text()
    if user_message is None:
        raise HTTPException(status_code=400, detail="messages must include at least one user message")
    try:
        user_message = services.guardrails.check_input([{"role": "user", "content": user_message}])[0]["content"]
        messages = services.guardrails.check_input(_context_messages(manager, db, conversation, user_message))
    except GuardrailViolation as exc:
        record.fail(400, str(exc), "blocked")
        raise HTTPException(status_code=400, detail=str(exc))
    return manager, conversation, user_message, messages


def _context_messages(
    manager: ConversationManager, db: Session, conversation: Conversation, user_message: str
) -> List[dict]:
    recent = manager.get_recent_messages(conversation.id)
    recent_dicts = [{"role": m.role, "content": m.content} for m in recent]
    summary = manager.get_conversation_summary(conversation.id)
    memories = [m.content for m in repository.get_memories(db, conversation.user_id, conversation.project)]
    return build_context(summary, memories, recent_dicts, user_message)


async def maybe_summarize(
    manager: ConversationManager, model_router: ModelRouter, conversation_id: str,
    request_model: str, principal: Optional[Principal], services: GatewayServices,
) -> None:
    """Best-effort: never let a failed (re)summarization turn into a failed
    chat response. Accounted as its own `summary` request."""
    message_count = manager.get_message_count(conversation_id)
    if not should_summarize(message_count):
        return
    summary_record = UsageRecord(
        f"req_{uuid.uuid4().hex}", "summary", request_model,
        principal=principal, conversation_id=conversation_id,
    )
    try:
        admit(request_model, principal, services, manager.db, summary_record)
        conversation = manager.get_conversation(conversation_id)
        expected_version = conversation.version
        recent_messages = manager.get_recent_messages(conversation_id, limit=100)
        message_dicts = [{"role": m.role, "content": m.content} for m in recent_messages]
        release_connection(manager.db)
        with provider_errors(summary_record):
            result = await summarize_conversation(
                model_router, message_dicts, conversation.summary or "", request_model
            )
        summary_record.served_by(result)
        summary = services.guardrails.check_output(result.content.strip())
        manager.update_summary(conversation_id, summary, expected_version)
        logger.info("conversation summarized: conversation_id=%s message_count=%d", conversation_id, message_count)
    except Exception as exc:  # noqa: BLE001
        if summary_record.status == "success":
            summary_record.fail(502, f"summarization failed: {type(exc).__name__}")
        logger.exception("summarization failed: conversation_id=%s", conversation_id)
    finally:
        finish(manager.db, summary_record, services.config.pricing, services.limiter, services.usage_writer)
