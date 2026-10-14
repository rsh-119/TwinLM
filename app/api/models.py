"""OpenAI-compatible model listing - the `model` values this key may use
(any `provider/model` is accepted too). `capabilities` is a TwinLM
extension: what at least one of the model's deployments supports, or null
when unknown."""
from typing import Optional

from fastapi import APIRouter, Depends

from app.auth import Principal, authenticate
from app.router.model_router import ModelRouter, get_model_router

router = APIRouter(prefix="/v1", tags=["models"])


@router.get("/models")
def list_models(
    model_router: ModelRouter = Depends(get_model_router),
    principal: Optional[Principal] = Depends(authenticate),
) -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "created": 0,
                "owned_by": "twinlm",
                "capabilities": model_router.capabilities(model_id),
            }
            for model_id in model_router.model_ids
            if principal is None or principal.may_use(model_id)
        ],
    }
