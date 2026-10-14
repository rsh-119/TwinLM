"""Server-rendered UI for trying the gateway without curl or Swagger.

Reuses the existing JSON endpoints' logic directly (e.g. calls
`chat_completions` as a plain function) instead of duplicating it - these
routes are just a thin HTML layer on top.
"""
import json
from contextlib import aclosing
from pathlib import Path
from typing import AsyncIterator, List
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.api.chat import chat_completions, prime_stream, stream_chat_turn
from app.api.common import GatewayServices, get_services
from app.database import repository
from app.database.database import get_db
from app.memory.conversation_manager import ConversationManager, ConversationNotFoundError
from app.router.model_router import ModelRouter, get_model_router
from app.schemas.chat import ChatCompletionRequest, ChatMessageIn
from app.web.formatting import render_message

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
templates.env.filters["render_message"] = render_message

_LABEL_MAX_LEN = 42

# Single-user placeholder until real sign-in exists. Swap this one function
# for a real auth lookup (session/cookie/JWT) later - nothing else in this
# file needs to change.
DEFAULT_USER_ID = "default_user"


def _current_user_id() -> str:
    return DEFAULT_USER_ID


def _sidebar_items(db: Session, user_id: str) -> List[dict]:
    """Conversation list + a short preview label, for the persistent sidebar.

    The label is the first line of the conversation's first message so a
    user can recognize a conversation without knowing its id.
    """
    items = []
    for conversation in repository.get_conversations_by_user(db, user_id):
        first = repository.get_first_message(db, conversation.id)
        if first:
            label = first.content.strip().splitlines()[0]
            if len(label) > _LABEL_MAX_LEN:
                label = label[:_LABEL_MAX_LEN].rstrip() + "…"
        else:
            label = conversation.id
        items.append({"id": conversation.id, "label": label})
    return items


@router.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    user_id = _current_user_id()
    return templates.TemplateResponse(
        request,
        "home.html",
        {"sidebar_conversations": _sidebar_items(db, user_id), "active_conversation_id": None},
    )


@router.post("/conversations")
def create_conversation_ui(db: Session = Depends(get_db)):
    conversation = ConversationManager(db).create_conversation(_current_user_id())
    return RedirectResponse(f"/conversations/{conversation.id}", status_code=303)


@router.get("/conversations/{conversation_id}")
def view_conversation(
    conversation_id: str,
    request: Request,
    error: str = "",
    db: Session = Depends(get_db),
):
    manager = ConversationManager(db)
    try:
        conversation = manager.get_conversation(conversation_id)
    except ConversationNotFoundError:
        return RedirectResponse("/", status_code=303)

    messages = manager.get_recent_messages(conversation_id, limit=100)
    memories = repository.get_memories(db, conversation.user_id)
    return templates.TemplateResponse(
        request,
        "conversation.html",
        {
            "conversation": conversation,
            "messages": messages,
            "memories": memories,
            "total_tokens": manager.get_total_tokens(conversation_id),
            "active_conversation_id": conversation.id,
            "sidebar_conversations": _sidebar_items(db, conversation.user_id),
            "error": error,
        },
    )


@router.post("/conversations/{conversation_id}/chat")
async def send_message_ui(
    conversation_id: str,
    content: str = Form(...),
    model: str = Form("auto"),
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_model_router),
    services: GatewayServices = Depends(get_services),
):
    body = ChatCompletionRequest(
        conversation_id=conversation_id,
        model=model,
        messages=[ChatMessageIn(role="user", content=content)],
    )
    try:
        await chat_completions(
            body, response=None, db=db, model_router=model_router, principal=None, services=services
        )
    except HTTPException as exc:
        return RedirectResponse(
            f"/conversations/{conversation_id}?error={quote(str(exc.detail))}", status_code=303
        )
    return RedirectResponse(f"/conversations/{conversation_id}", status_code=303)


@router.post("/conversations/{conversation_id}/chat/stream")
async def send_message_stream_ui(
    conversation_id: str,
    content: str = Form(...),
    model: str = Form("auto"),
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_model_router),
    services: GatewayServices = Depends(get_services),
):
    """What the composer's JS (conversation.html) actually calls: same turn
    as `send_message_ui` above, but streamed - each event carries the
    *whole* reply rendered so far (via `render_message`, the same helper
    used for a stored message), not just the new text, so the browser can
    just drop it in with `innerHTML` and have markdown/code formatting
    build up live instead of only appearing once the reply is done.
    """
    body = ChatCompletionRequest(
        conversation_id=conversation_id,
        model=model,
        messages=[ChatMessageIn(role="user", content=content)],
    )
    chunks = await prime_stream(stream_chat_turn(body, db, model_router, services=services))

    async def _events() -> AsyncIterator[str]:
        accumulated = ""
        async with aclosing(chunks):
            async for chunk in chunks:
                if chunk.delta:
                    accumulated += chunk.delta
                payload = {
                    "provider": chunk.provider,
                    "model": chunk.model,
                    "done": chunk.done,
                    "html": render_message(accumulated),
                }
                if chunk.usage:
                    payload["usage"] = chunk.usage._asdict()
                yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(_events(), media_type="text/event-stream")


@router.post("/conversations/{conversation_id}/delete")
def delete_conversation_ui(
    conversation_id: str,
    db: Session = Depends(get_db),
):
    manager = ConversationManager(db)
    try:
        manager.delete_conversation(conversation_id)
    except ConversationNotFoundError:
        pass  # already gone - fall through to the redirect below
    return RedirectResponse("/", status_code=303)


@router.post("/memories")
def add_memory_ui(
    user_id: str = Form(...),
    content: str = Form(...),
    conversation_id: str = Form(...),
    db: Session = Depends(get_db),
):
    repository.create_memory(db, user_id=user_id, content=content)
    return RedirectResponse(f"/conversations/{conversation_id}", status_code=303)
