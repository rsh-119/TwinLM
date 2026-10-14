"""Conversation endpoints: create a conversation and read it back. Access
is scoped by app/api/stateful.py (project, and user for user-bound keys);
mounted only when STATEFUL_ENABLED=true."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.stateful import manager_for, owner_for
from app.database import repository
from app.auth import Principal, authenticate
from app.database.database import get_db
from app.memory.conversation_manager import ConversationNotFoundError

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


class CreateConversationRequest(BaseModel):
    """Request body for POST /v1/conversations. `user_id` may be omitted
    with a user-bound key (it defaults to the key's user)."""

    user_id: Optional[str] = Field(default=None, min_length=1, max_length=128)


class CreateConversationResponse(BaseModel):
    """Response body for POST /v1/conversations."""

    conversation_id: str


class ConversationResponse(BaseModel):
    """Response body for GET /v1/conversations/{conversation_id}."""

    conversation_id: str
    user_id: str
    summary: str
    total_tokens: int


class MessageResponse(BaseModel):
    """One stored message, as returned by GET .../messages. Token fields are
    null for user messages, and for assistant ones from before usage
    tracking existed or whose provider didn't report it."""

    id: str
    role: str
    content: str
    model: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


@router.post("", response_model=CreateConversationResponse, status_code=201)
def create_conversation(
    body: CreateConversationRequest,
    db: Session = Depends(get_db),
    principal: Optional[Principal] = Depends(authenticate),
) -> CreateConversationResponse:
    """Create a new, empty conversation for a user."""
    user_id = owner_for(principal, body.user_id)
    conversation = manager_for(db, principal).create_conversation(user_id)
    return CreateConversationResponse(conversation_id=conversation.id)


@router.get("/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    principal: Optional[Principal] = Depends(authenticate),
) -> ConversationResponse:
    """Fetch a conversation's metadata (user, stored summary)."""
    manager = manager_for(db, principal)
    try:
        conversation = manager.get_conversation(conversation_id)
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="conversation not found")
    return ConversationResponse(
        conversation_id=conversation.id,
        user_id=conversation.user_id,
        summary=conversation.summary,
        total_tokens=manager.get_total_tokens(conversation_id),
    )


@router.delete("/{conversation_id}", status_code=204, response_model=None)
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    principal: Optional[Principal] = Depends(authenticate),
) -> None:
    """Permanently delete a conversation and all of its messages."""
    manager = manager_for(db, principal)
    try:
        manager.delete_conversation(conversation_id)
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="conversation not found")


@router.get("/{conversation_id}/messages", response_model=List[MessageResponse])
def get_conversation_messages(
    conversation_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    before: Optional[str] = None,
    db: Session = Depends(get_db),
    principal: Optional[Principal] = Depends(authenticate),
) -> List[MessageResponse]:
    """Most recent messages, oldest first. Pass the first returned ID as
    `before` to fetch the preceding page."""
    manager = manager_for(db, principal)
    try:
        manager.get_conversation(conversation_id)
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="conversation not found")

    messages = repository.get_messages(db, conversation_id, limit=limit, before_id=before)
    return [
        MessageResponse(
            id=m.id,
            role=m.role,
            content=m.content,
            model=m.model,
            prompt_tokens=m.prompt_tokens,
            completion_tokens=m.completion_tokens,
            total_tokens=m.total_tokens,
        )
        for m in messages
    ]
