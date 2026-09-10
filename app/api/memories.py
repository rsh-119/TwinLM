"""User memory endpoints: simple long-term facts remembered per user.

No embeddings or semantic search in V1 - just a flat list of facts per
user, included verbatim in every conversation's context (see
`app/memory/context_builder.py` and how `app/api/chat.py` loads them).
"""
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import repository
from app.database.database import get_db

router = APIRouter(prefix="/v1/memories", tags=["memories"])


class CreateMemoryRequest(BaseModel):
    """Request body for POST /v1/memories."""

    user_id: str
    content: str


class MemoryResponse(BaseModel):
    """A single stored memory fact."""

    id: str
    user_id: str
    content: str


@router.post("", response_model=MemoryResponse, status_code=201)
def create_memory(body: CreateMemoryRequest, db: Session = Depends(get_db)) -> MemoryResponse:
    """Store a new long-term memory fact for a user."""
    memory = repository.create_memory(db, user_id=body.user_id, content=body.content)
    return MemoryResponse(id=memory.id, user_id=memory.user_id, content=memory.content)


@router.get("/{user_id}", response_model=List[MemoryResponse])
def list_memories(user_id: str, db: Session = Depends(get_db)) -> List[MemoryResponse]:
    """Return all stored memory facts for a user, oldest first."""
    memories = repository.get_memories(db, user_id)
    return [MemoryResponse(id=m.id, user_id=m.user_id, content=m.content) for m in memories]
