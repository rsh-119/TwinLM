"""OpenAI-compatible `POST /v1/embeddings`. Routed like chat: aliases,
retries, circuit breakers and fallback across targets that support
embeddings (targets that don't are skipped)."""
import base64
import struct
from typing import List, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.common import GatewayServices, admit, get_services, provider_errors
from app.auth import Principal, authenticate
from app.database.database import get_db, release_connection
from app.gateway.guardrails import GuardrailViolation
from app.gateway.usage import UsageRecord, finish
from app.observability import current_request_id
from app.router.model_router import ModelRouter, get_model_router
from app.schemas.chat import _StrictWire

router = APIRouter(prefix="/v1", tags=["embeddings"])

_MAX_INPUTS = 2048


class EmbeddingRequest(_StrictWire):
    model: str
    input: Union[str, List[str]]
    # The OpenAI SDK asks for base64 by default and decodes it itself.
    encoding_format: Optional[Literal["float", "base64"]] = None
    user: Optional[str] = None


class EmbeddingItem(BaseModel):
    object: Literal["embedding"] = "embedding"
    index: int
    embedding: Union[List[float], str]


class EmbeddingUsage(BaseModel):
    prompt_tokens: int = 0
    total_tokens: int = 0


class EmbeddingResponse(BaseModel):
    object: Literal["list"] = "list"
    data: List[EmbeddingItem]
    model: str
    provider: str
    usage: EmbeddingUsage = Field(default_factory=EmbeddingUsage)


@router.post("/embeddings", response_model=EmbeddingResponse)
async def create_embeddings(
    body: EmbeddingRequest,
    db: Session = Depends(get_db),
    model_router: ModelRouter = Depends(get_model_router),
    principal: Optional[Principal] = Depends(authenticate),
    services: GatewayServices = Depends(get_services),
) -> EmbeddingResponse:
    record = UsageRecord(current_request_id(), "embeddings", body.model, principal=principal)
    try:
        texts = [body.input] if isinstance(body.input, str) else body.input
        if not texts or len(texts) > _MAX_INPUTS:
            raise HTTPException(status_code=400, detail=f"input must contain 1-{_MAX_INPUTS} items")
        admit(body.model, principal, services, db, record)
        try:
            checked = services.guardrails.check_input([{"role": "user", "content": t} for t in texts])
        except GuardrailViolation as exc:
            record.fail(400, str(exc), "blocked")
            raise HTTPException(status_code=400, detail=str(exc))

        release_connection(db)
        with provider_errors(record):
            result = await model_router.embed(body.model, [m["content"] for m in checked])
        record.served_by(result)
        usage = result.usage
        return EmbeddingResponse(
            data=[
                EmbeddingItem(index=i, embedding=_encode(v, body.encoding_format))
                for i, v in enumerate(result.vectors)
            ],
            model=result.model,
            provider=result.provider,
            usage=EmbeddingUsage(
                prompt_tokens=usage.prompt_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
            ),
        )
    except HTTPException as exc:
        if record.status == "success":
            record.fail(exc.status_code, str(exc.detail))
        raise
    finally:
        finish(db, record, services.config.pricing, services.limiter, services.usage_writer)


def _encode(vector: List[float], encoding_format: Optional[str]) -> Union[List[float], str]:
    """base64 = little-endian float32 bytes, as OpenAI returns it."""
    if encoding_format == "base64":
        return base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode()
    return vector
