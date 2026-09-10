"""Chat completions endpoint: the gateway's core request flow.

Load conversation -> load history/summary/memories -> build context ->
route to a provider (with automatic fallback for model="auto") -> store
the exchange -> return a normalized response, regardless of which provider
actually answered.
"""
import logging
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import repository
from app.database.database import get_db
from app.memory.context_builder import build_context
from app.memory.conversation_manager import ConversationManager, ConversationNotFoundError
from app.memory.summarizer import should_summarize, summarize_conversation
from app.providers.base import ProviderUnavailableError
from app.router.model_router import ModelRouter, NoProviderAvailableError, get_model_router
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse, ChatResponseMessage

logger = logging.getLogger("llm_gateway.api.chat")

router = APIRouter(prefix="/v1", tags=["chat"])


@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    body: ChatCompletionRequest,
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_model_router),
) -> ChatCompletionResponse:
    """Handle one conversation turn, preserving context across providers."""
    request_id = f"req_{uuid.uuid4().hex[:12]}"
    manager = ConversationManager(db)

    # 1. Load conversation.
    try:
        conversation = manager.get_conversation(body.conversation_id)
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="conversation not found")

    user_message = _latest_user_message(body)
    if user_message is None:
        raise HTTPException(
            status_code=400, detail="messages must include at least one user message"
        )

    # 2. Load previous messages, conversation summary, and user memories.
    recent = manager.get_recent_messages(conversation.id)
    recent_dicts = [{"role": m.role, "content": m.content} for m in recent]
    summary = manager.get_conversation_summary(conversation.id)
    memories = [m.content for m in repository.get_memories(db, conversation.user_id)]

    # 3. Build context.
    context_messages = build_context(summary, memories, recent_dicts, user_message)

    # 4 & 5. Select a provider and call it (fallback happens inside the
    # router for model="auto").
    started = time.monotonic()
    try:
        result = await model_router.route(body.model, context_messages)
    except ValueError as exc:
        logger.warning(
            "request_id=%s conversation_id=%s status=invalid_request error=%s",
            request_id, conversation.id, exc,
        )
        raise HTTPException(status_code=400, detail=str(exc))
    except (ProviderUnavailableError, NoProviderAvailableError):
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.error(
            "request_id=%s conversation_id=%s model=%s latency=%dms status=unavailable",
            request_id, conversation.id, body.model, latency_ms,
        )
        raise HTTPException(status_code=503, detail="all providers are currently unavailable")
    except Exception:  # noqa: BLE001 - never leak raw provider errors to clients
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.exception(
            "request_id=%s conversation_id=%s model=%s latency=%dms status=error",
            request_id, conversation.id, body.model, latency_ms,
        )
        raise HTTPException(status_code=502, detail="upstream provider error")

    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "request_id=%s conversation_id=%s provider=%s model=%s latency=%dms status=success",
        request_id, conversation.id, result.provider, result.model, latency_ms,
    )

    # 6 & 7. Store the user's message and the assistant's response.
    manager.save_message(conversation.id, "user", user_message)
    manager.save_message(conversation.id, "assistant", result.content, model=result.provider)

    # Periodically compact history into a summary so future requests don't
    # need to resend the entire conversation (see MAX_RECENT_MESSAGES).
    # Never let a summarization failure break the chat response itself.
    message_count = manager.get_message_count(conversation.id)
    if should_summarize(message_count):
        try:
            all_messages = manager.get_all_messages(conversation.id)
            message_dicts = [{"role": m.role, "content": m.content} for m in all_messages]
            summary = await summarize_conversation(model_router, message_dicts)
            manager.update_summary(conversation.id, summary)
            logger.info(
                "request_id=%s conversation_id=%s status=summarized message_count=%d",
                request_id, conversation.id, message_count,
            )
        except Exception:  # noqa: BLE001 - summarization is best-effort
            logger.exception(
                "request_id=%s conversation_id=%s status=summarization_failed",
                request_id, conversation.id,
            )

    # 8. Return a normalized response - identical shape regardless of provider.
    return ChatCompletionResponse(
        id=request_id,
        conversation_id=conversation.id,
        model=result.model,
        provider=result.provider,
        message=ChatResponseMessage(content=result.content),
    )


def _latest_user_message(body: ChatCompletionRequest) -> Optional[str]:
    """Return the content of the last user-role message in the request, if any."""
    for message in reversed(body.messages):
        if message.role == "user":
            return message.content
    return None
