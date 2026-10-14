"""User memory endpoints - a flat list of facts per user, no embeddings or
semantic search. See app/memory/context_builder.py for how these get used.
Scoped like conversations (app/api/stateful.py); mounted only when
STATEFUL_ENABLED=true."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import repository
from app.auth import Principal, authenticate, project_of
from app.database.database import get_db
from app.api.common import GatewayServices, get_services
from app.api.stateful import owner_for
from app.gateway.guardrails import GuardrailViolation

router = APIRouter(prefix="/v1/memories", tags=["memories"])


class CreateMemoryRequest(BaseModel):
    """Request body for POST /v1/memories. `user_id` may be omitted with a
    user-bound key."""

    user_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    content: str = Field(min_length=1, max_length=4000)


class MemoryResponse(BaseModel):
    """A single stored memory fact."""

    id: str
    user_id: str
    content: str


@router.post("", response_model=MemoryResponse, status_code=201)
def create_memory(
    body: CreateMemoryRequest,
    db: Session = Depends(get_db),
    principal: Optional[Principal] = Depends(authenticate),
    services: GatewayServices = Depends(get_services),
) -> MemoryResponse:
    """Store a new long-term memory fact for a user."""
    user_id = owner_for(principal, body.user_id)
    try:
        checked = services.guardrails.check_input([{"role": "user", "content": body.content}])
    except GuardrailViolation as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    memory = repository.create_memory(db, user_id=user_id, content=checked[0]["content"], project=project_of(principal))
    return MemoryResponse(id=memory.id, user_id=memory.user_id, content=memory.content)


@router.get("/{user_id}", response_model=List[MemoryResponse])
def list_memories(
    user_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    before: Optional[str] = None,
    db: Session = Depends(get_db),
    principal: Optional[Principal] = Depends(authenticate),
) -> List[MemoryResponse]:
    """Return recent facts; `before` fetches the preceding page."""
    user_id = owner_for(principal, user_id)
    memories = repository.get_memories(db, user_id, project_of(principal), limit, before)
    return [MemoryResponse(id=m.id, user_id=m.user_id, content=m.content) for m in memories]
