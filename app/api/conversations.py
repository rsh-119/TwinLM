"""Conversation endpoints: create a conversation and read it back.

These exist alongside the chat endpoint (not as a separately numbered
build step) because the chat flow requires a conversation to already
exist - a client always creates one here first, then sends turns to
/v1/chat/completions using its id.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import repository
from app.database.database import get_db
from app.memory.conversation_manager import ConversationManager, ConversationNotFoundError

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


class CreateConversationRequest(BaseModel):
    """Request body for POST /v1/conversations."""

    user_id: str


class CreateConversationResponse(BaseModel):
    """Response body for POST /v1/conversations."""

    conversation_id: str


class ConversationResponse(BaseModel):
    """Response body for GET /v1/conversations/{conversation_id}."""

    conversation_id: str
    user_id: str
    summary: str


class MessageResponse(BaseModel):
    """One stored message, as returned by GET .../messages."""

    role: str
    content: str
    model: Optional[str] = None


@router.post("", response_model=CreateConversationResponse, status_code=201)
def create_conversation(
    body: CreateConversationRequest, db: Session = Depends(get_db)
) -> CreateConversationResponse:
    """Create a new, empty conversation for a user."""
    manager = ConversationManager(db)
    conversation = manager.create_conversation(body.user_id)
    return CreateConversationResponse(conversation_id=conversation.id)


@router.get("/{conversation_id}", response_model=ConversationResponse)
def get_conversation(conversation_id: str, db: Session = Depends(get_db)) -> ConversationResponse:
    """Fetch a conversation's metadata (user, stored summary)."""
    manager = ConversationManager(db)
    try:
        conversation = manager.get_conversation(conversation_id)
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="conversation not found")
    return ConversationResponse(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        summary=conversation.summary,
    )


@router.get("/{conversation_id}/messages", response_model=List[MessageResponse])
def get_conversation_messages(
    conversation_id: str, db: Session = Depends(get_db)
) -> List[MessageResponse]:
    """Fetch the full stored message history for a conversation, oldest first.

    Unlike the context the gateway sends to a provider, this is not limited
    to the most recent messages - it's meant for inspecting/debugging a
    conversation in full.
    """
    manager = ConversationManager(db)
    try:
        manager.get_conversation(conversation_id)
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="conversation not found")

    messages = repository.get_messages(db, conversation_id)
    return [
        MessageResponse(role=m.role, content=m.content, model=m.model) for m in messages
    ]
