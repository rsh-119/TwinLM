"""Chat completions endpoint: load conversation -> build context -> route
to a provider (auto-fallback if needed) -> store the exchange -> respond.

`body.stream=True` swaps the last two steps for their streaming
equivalents (see `stream_chat_turn` below) and the response for one
Server-Sent Events - same request shape, same fallback rules, just
delivered incrementally instead of as one JSON blob. That's also what
powers the web UI's live "typing" effect (app/web/routes.py).
"""
import json
import logging
import time
import uuid
from typing import AsyncIterator, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import repository
from app.database.database import get_db
from app.database.models import Conversation
from app.memory.context_builder import build_context
from app.memory.conversation_manager import ConversationManager, ConversationNotFoundError
from app.memory.summarizer import should_summarize, summarize_conversation
from app.providers.base import ProviderUnavailableError
from app.router.model_router import ModelRouter, NoProviderAvailableError, RouteChunk, get_model_router
from app.schemas.chat import ChatCompletionRequest, ChatCompletionResponse, ChatResponseMessage

logger = logging.getLogger("twinlm.api.chat")

router = APIRouter(prefix="/v1", tags=["chat"])


@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    body: ChatCompletionRequest,
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_model_router),
) -> Union[ChatCompletionResponse, StreamingResponse]:
    if body.stream:
        return await _stream_chat_completions(body, db, model_router)

    request_id = f"req_{uuid.uuid4().hex[:12]}"
    manager = ConversationManager(db)
    conversation = _get_conversation_or_404(manager, body.conversation_id)

    user_message = _latest_user_message(body)
    if user_message is None:
        raise HTTPException(
            status_code=400, detail="messages must include at least one user message"
        )

    context_messages = _load_context_messages(manager, db, conversation, user_message)

    # fallback (if any) happens inside the router for model="auto"
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
    except Exception:  # noqa: BLE001
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

    manager.save_message(conversation.id, "user", user_message)
    manager.save_message(conversation.id, "assistant", result.content, model=result.provider)

    await _maybe_summarize(manager, model_router, conversation.id, request_id)

    return ChatCompletionResponse(
        id=request_id,
        conversation_id=conversation.id,
        model=result.model,
        provider=result.provider,
        message=ChatResponseMessage(content=result.content),
    )


async def _stream_chat_completions(
    body: ChatCompletionRequest, db: Session, model_router: ModelRouter
) -> StreamingResponse:
    """The `"stream": true` branch of `/chat/completions` - same request,
    but the reply comes back as SSE (`data: {"provider", "model", "delta",
    "done"}` lines) instead of one JSON body. All the actual work is
    `stream_chat_turn`; this just primes it (see `prime_stream`) so a setup
    failure still turns into a normal 400/404/503, then formats the rest
    of the chunks onto the wire.
    """
    chunks = await prime_stream(stream_chat_turn(body, db, model_router))

    async def _sse() -> AsyncIterator[str]:
        async for chunk in chunks:
            payload = {
                "provider": chunk.provider,
                "model": chunk.model,
                "delta": chunk.delta,
                "done": chunk.done,
            }
            yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(_sse(), media_type="text/event-stream")


async def stream_chat_turn(
    body: ChatCompletionRequest, db: Session, model_router: ModelRouter
) -> AsyncIterator[RouteChunk]:
    """Streaming counterpart to the body of `chat_completions` above -
    shared by the JSON SSE branch and the web UI's own streaming endpoint
    (app/web/routes.py), which reuses this and re-renders each chunk as
    markdown instead of sending raw text.

    Builds context exactly the same way, streams the reply from the
    router chunk by chunk, and once the stream ends, persists the
    exchange and runs best-effort summarization - the same bookkeeping
    `chat_completions` does after its one blocking call.

    Setup failures (unknown conversation, no user message, no reachable
    provider) raise HTTPException on the *first* item this yields, before
    anything has gone out over the wire - see `prime_stream`, which every
    caller should route through for that reason. A failure after that
    point can't change the response anymore, so it's logged instead.
    """
    request_id = f"req_{uuid.uuid4().hex[:12]}"
    manager = ConversationManager(db)
    conversation = _get_conversation_or_404(manager, body.conversation_id)

    user_message = _latest_user_message(body)
    if user_message is None:
        raise HTTPException(
            status_code=400, detail="messages must include at least one user message"
        )

    context_messages = _load_context_messages(manager, db, conversation, user_message)

    started = time.monotonic()
    collected: List[str] = []
    final_provider: Optional[str] = None
    final_model: Optional[str] = None

    try:
        async for chunk in model_router.route_stream(body.model, context_messages):
            if chunk.delta:
                collected.append(chunk.delta)
            if chunk.done:
                final_provider, final_model = chunk.provider, chunk.model
            yield chunk
    except ValueError as exc:
        # only reachable before the first chunk - see ModelRouter.route_stream
        logger.warning(
            "request_id=%s conversation_id=%s status=invalid_request error=%s",
            request_id, conversation.id, exc,
        )
        raise HTTPException(status_code=400, detail=str(exc))
    except (ProviderUnavailableError, NoProviderAvailableError):
        # only reachable before the first chunk - see ModelRouter.route_stream
        latency_ms = int((time.monotonic() - started) * 1000)
        logger.error(
            "request_id=%s conversation_id=%s model=%s latency=%dms status=unavailable",
            request_id, conversation.id, body.model, latency_ms,
        )
        raise HTTPException(status_code=503, detail="all providers are currently unavailable")

    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "request_id=%s conversation_id=%s provider=%s model=%s latency=%dms status=success",
        request_id, conversation.id, final_provider, final_model, latency_ms,
    )

    full_content = "".join(collected)
    try:
        manager.save_message(conversation.id, "user", user_message)
        manager.save_message(conversation.id, "assistant", full_content, model=final_provider)
    except Exception:  # noqa: BLE001 - the reply already reached the client either way
        logger.exception(
            "request_id=%s conversation_id=%s status=persist_failed", request_id, conversation.id
        )
        return

    await _maybe_summarize(manager, model_router, conversation.id, request_id)


async def prime_stream(gen: AsyncIterator[RouteChunk]) -> AsyncIterator[RouteChunk]:
    """Pulls the first item out of `gen` right now, before the caller
    constructs a StreamingResponse. `stream_chat_turn`'s setup work
    (conversation lookup, context building, connecting to a provider) all
    happens on that first pull, so an HTTPException it raises still turns
    into a normal status code - once a StreamingResponse has sent its
    headers (status 200), that's no longer possible.

    Returns an iterator that replays the peeked-at item followed by the
    rest of `gen`, so the caller never notices the peek happened.
    """
    try:
        first = await gen.__anext__()
    except StopAsyncIteration:
        first = None

    async def _replay() -> AsyncIterator[RouteChunk]:
        if first is not None:
            yield first
        async for item in gen:
            yield item

    return _replay()


def _get_conversation_or_404(manager: ConversationManager, conversation_id: str) -> Conversation:
    try:
        return manager.get_conversation(conversation_id)
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="conversation not found")


def _load_context_messages(
    manager: ConversationManager, db: Session, conversation: Conversation, user_message: str
) -> List[dict]:
    recent = manager.get_recent_messages(conversation.id)
    recent_dicts = [{"role": m.role, "content": m.content} for m in recent]
    summary = manager.get_conversation_summary(conversation.id)
    memories = [m.content for m in repository.get_memories(db, conversation.user_id)]
    return build_context(summary, memories, recent_dicts, user_message)


async def _maybe_summarize(
    manager: ConversationManager, model_router: ModelRouter, conversation_id: str, request_id: str
) -> None:
    """Best-effort, same as before: never let a failed (re)summarization
    turn into a failed chat response."""
    message_count = manager.get_message_count(conversation_id)
    if not should_summarize(message_count):
        return
    try:
        all_messages = manager.get_all_messages(conversation_id)
        message_dicts = [{"role": m.role, "content": m.content} for m in all_messages]
        summary = await summarize_conversation(model_router, message_dicts)
        manager.update_summary(conversation_id, summary)
        logger.info(
            "request_id=%s conversation_id=%s status=summarized message_count=%d",
            request_id, conversation_id, message_count,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "request_id=%s conversation_id=%s status=summarization_failed",
            request_id, conversation_id,
        )


def _latest_user_message(body: ChatCompletionRequest) -> Optional[str]:
    """Return the content of the last user-role message in the request, if any."""
    for message in reversed(body.messages):
        if message.role == "user":
            return message.content
    return None
