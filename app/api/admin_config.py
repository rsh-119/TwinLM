"""Admin API for runtime configuration: providers (with encrypted API keys),
model aliases and prices managed from the admin panel. Every write is
validated by building the resulting config and router before it is
committed, then applied immediately - no restart (app/gateway/runtime.py)."""
import asyncio
import json
import re
import time
from typing import List, Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.common import GatewayServices, get_services
from app.auth import require_admin
from app.config import get_settings
from app.database.database import get_db
from app.database.models import AliasOverride, PriceOverride, ProviderConfigRow
from app.gateway import runtime, secret_box
from app.gateway.config import Capability, ProviderConfig
from app.gateway.presets import PRESETS, chat_models, recommended_model
from app.providers.gemini import GEMINI_API_BASE
from app.router.model_router import build_provider, get_model_router

router = APIRouter(prefix="/admin/config", tags=["admin"], dependencies=[Depends(require_admin)])

NAME = r"^[a-z0-9][a-z0-9_.-]{0,39}$"  # no "/": targets are "provider/model"


class ProviderIn(BaseModel):
    name: str = Field(pattern=NAME)
    preset: Optional[str] = None
    type: Optional[Literal["gemini", "openai_compatible", "mock"]] = None
    base_url: Optional[str] = None
    default_model: Optional[str] = Field(default=None, min_length=1)
    api_key: Optional[str] = Field(default=None, min_length=1)
    capabilities: Optional[List[Capability]] = None
    timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    enabled: bool = True
    auth_header: Optional[Literal["authorization", "api-key"]] = None


class ProviderPatch(BaseModel):
    base_url: Optional[str] = None
    default_model: Optional[str] = Field(default=None, min_length=1)
    api_key: Optional[str] = Field(default=None, min_length=1)  # rotate
    capabilities: Optional[List[Capability]] = None
    timeout_seconds: Optional[float] = Field(default=None, gt=0, le=600)
    enabled: Optional[bool] = None


class TestIn(BaseModel):
    """Either an existing provider `name`, or the fields of a new one."""

    name: Optional[str] = None
    preset: Optional[str] = None
    type: Optional[Literal["gemini", "openai_compatible", "mock"]] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None


class AliasIn(BaseModel):
    targets: List[str] = Field(min_length=1)
    max_output_tokens: Optional[int] = Field(default=None, gt=0)
    cache_ttl_seconds: Optional[float] = Field(default=None, ge=0)


class PriceIn(BaseModel):
    key: str = Field(pattern=r"^[^/\s]+/\S+$")  # provider/model
    input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)


# ---- helpers ---------------------------------------------------------------

def _commit_and_apply(db: Session, services: GatewayServices) -> None:
    """Validate the session's pending changes as a whole config, then commit
    and make them live; on an invalid result nothing is stored."""
    try:
        db.flush()
        config, new_router = runtime.build(db)
    except (ValueError, secret_box.SecretUnavailable) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"invalid configuration: {exc}")
    db.commit()
    runtime.apply(config, new_router, services)
    runtime.announce()


def _resolve(preset: Optional[str], type_: Optional[str], base_url: Optional[str], model: Optional[str]):
    p = PRESETS.get(preset or "")
    if preset and p is None:
        raise HTTPException(status_code=400, detail=f"unknown preset {preset!r}")
    type_ = type_ or (p["type"] if p else "openai_compatible")
    base_url = base_url if base_url is not None else (p["base_url"] if p else None)
    model = model or (p["default_model"] if p else None)
    if type_ == "openai_compatible" and not base_url:
        raise HTTPException(status_code=400, detail="base_url is required for an OpenAI-compatible provider")
    if not model:
        raise HTTPException(status_code=400, detail="default_model is required")
    return p, type_, base_url, model


def _encrypt(api_key: str) -> str:
    try:
        return secret_box.encrypt(api_key)
    except secret_box.SecretUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


def _uses(spec: str, provider: str) -> bool:
    return spec == provider or spec.startswith(f"{provider}/")


def _health(name: str) -> Optional[str]:
    health = get_model_router().health.get(name)
    return health.state.value if health else None


# ---- presets & providers ---------------------------------------------------

@router.get("/presets")
def presets() -> dict:
    return PRESETS


@router.get("/providers")
def list_providers(db: Session = Depends(get_db)) -> List[dict]:
    base = runtime.base_config()
    out = [
        {"name": name, "source": "gateway.toml", "preset": None, "type": cfg.type, "base_url": cfg.base_url,
         "default_model": cfg.default_model, "capabilities": cfg.capabilities, "enabled": cfg.enabled,
         "key_hint": f"from {cfg.api_key_setting.upper()}" if cfg.api_key_setting else None,
         "timeout_seconds": cfg.timeout_seconds, "health": _health(name)}
        for name, cfg in base.providers.items()
    ]
    for row in db.query(ProviderConfigRow).order_by(ProviderConfigRow.created_at).all():
        out.append({"name": row.name, "source": "panel", "preset": row.preset, "type": row.type,
                    "base_url": row.base_url, "default_model": row.default_model,
                    "capabilities": json.loads(row.capabilities) if row.capabilities else None,
                    "enabled": row.enabled, "key_hint": row.key_hint, "timeout_seconds": row.timeout_seconds,
                    "health": _health(row.name)})
    return out


@router.post("/providers", status_code=201)
async def create_provider(
    body: ProviderIn, db: Session = Depends(get_db), services: GatewayServices = Depends(get_services)
) -> dict:
    config, _ = runtime.effective_config(db)
    if body.name in config.providers or body.name in config.models or body.name in config.deployments:
        raise HTTPException(status_code=409, detail=f"{body.name!r} is already a provider, alias or deployment")
    preset, type_, base_url, model = _resolve(body.preset, body.type, body.base_url, body.default_model)
    if preset and preset["needs_key"] and not body.api_key:
        raise HTTPException(status_code=400, detail=f"{preset['label']} needs an API key")
    row = ProviderConfigRow(
        name=body.name, preset=body.preset, type=type_, base_url=base_url, default_model=model,
        capabilities=json.dumps(body.capabilities if body.capabilities is not None
                                else (preset["capabilities"] if preset else None)),
        timeout_seconds=body.timeout_seconds, enabled=body.enabled,
        api_key_encrypted=_encrypt(body.api_key) if body.api_key else None,
        key_hint=secret_box.hint(body.api_key) if body.api_key else None,
        auth_header=body.auth_header or (preset["auth_header"] if preset else "authorization"),
    )
    if row.capabilities == "null":
        row.capabilities = None
    db.add(row)
    _commit_and_apply(db, services)
    return {"name": row.name, "key_hint": row.key_hint, "enabled": row.enabled}


@router.patch("/providers/{name}")
async def update_provider(
    name: str, body: ProviderPatch, db: Session = Depends(get_db),
    services: GatewayServices = Depends(get_services),
) -> dict:
    row = db.get(ProviderConfigRow, name)
    if row is None:
        detail = "providers from gateway.toml are edited in the file" if name in runtime.base_config().providers \
            else "provider not found"
        raise HTTPException(status_code=404, detail=detail)
    for field in ("base_url", "default_model", "timeout_seconds", "enabled"):
        value = getattr(body, field)
        if value is not None:
            setattr(row, field, value)
    if body.capabilities is not None:
        row.capabilities = json.dumps(body.capabilities)
    if body.api_key:
        row.api_key_encrypted = _encrypt(body.api_key)
        row.key_hint = secret_box.hint(body.api_key)
    _commit_and_apply(db, services)
    return {"name": row.name, "key_hint": row.key_hint, "enabled": row.enabled}


@router.delete("/providers/{name}")
async def delete_provider(
    name: str, db: Session = Depends(get_db), services: GatewayServices = Depends(get_services)
) -> dict:
    row = db.get(ProviderConfigRow, name)
    if row is None:
        raise HTTPException(status_code=404, detail="provider not found (gateway.toml providers can't be deleted here)")
    config, _ = runtime.effective_config(db)
    users = sorted(alias for alias, cfg in config.models.items()
                   if any(_uses(spec, name) for group in cfg.group_specs() for spec in group))
    if users:
        raise HTTPException(status_code=409, detail=f"remove {name!r} from these aliases first: {', '.join(users)}")
    db.delete(row)
    _commit_and_apply(db, services)
    return {"deleted": name}


async def _live_models(cfg: ProviderConfig, api_key: str) -> List[str]:
    """The provider's own model list (OpenAI-compatible GET /models, or
    Gemini's), so the panel offers models that exist today for this key.
    Empty when unsupported."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            if cfg.type == "gemini" and api_key:
                base = (cfg.base_url or GEMINI_API_BASE).rstrip("/")
                response = await client.get(f"{base}/models", params={"pageSize": 1000},
                                            headers={"x-goog-api-key": api_key})
                models = response.json().get("models", []) if response.status_code == 200 else []
                return sorted(m["name"].removeprefix("models/") for m in models if isinstance(m, dict)
                              and "generateContent" in m.get("supportedGenerationMethods", []))[:300]
            if cfg.type != "openai_compatible" or not cfg.base_url:
                return []
            auth = {"api-key": api_key} if cfg.auth_header == "api-key" else {"Authorization": f"Bearer {api_key}"}
            response = await client.get(f"{cfg.base_url.rstrip('/')}/models", headers=auth if api_key else {})
        data = response.json().get("data", []) if response.status_code == 200 else []
        return sorted(m["id"] for m in data if isinstance(m, dict) and isinstance(m.get("id"), str))[:300]
    except (httpx.HTTPError, ValueError, AttributeError, KeyError):
        return []


@router.post("/test")
async def test_provider(body: TestIn, db: Session = Depends(get_db)) -> dict:
    """One tiny request (max_tokens=1) with the given settings, plus the
    provider's live model list when it has one. Stores nothing."""
    if body.name:
        config, keys = runtime.effective_config(db)
        cfg = config.providers.get(body.name)
        if cfg is None:
            raise HTTPException(status_code=404, detail="provider not found")
        api_key = body.api_key or keys.get(body.name)
        model = body.model or cfg.default_model
    else:
        preset, type_, base_url, model = _resolve(body.preset, body.type, body.base_url, body.model)
        cfg = ProviderConfig(type=type_, base_url=base_url, default_model=model, timeout_seconds=20,
                             auth_header=preset["auth_header"] if preset else "authorization")
        api_key = body.api_key or ""
    provider = build_provider(body.name or "test", cfg, get_settings(), api_key)
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            provider.generate([{"role": "user", "content": "ping"}], model, {"max_tokens": 1}), timeout=25
        )
        out = {"ok": True, "model": model, "latency_ms": int((time.perf_counter() - started) * 1000),
               "reply": (result.content or "")[:100]}
    except Exception as exc:  # noqa: BLE001 - any failure is the answer
        out = {"ok": False, "model": model, "latency_ms": int((time.perf_counter() - started) * 1000),
               "error": f"{type(exc).__name__}: {exc}"[:500]}
    finally:
        await provider.aclose()
    out["models"] = chat_models(await _live_models(cfg, api_key or ""))  # for picking a chat model
    # pre-select the lightest model this key can use (the preset's list is lightest first)
    row = db.get(ProviderConfigRow, body.name) if body.name else None
    preset = PRESETS.get((row.preset if row else body.preset) or "")
    out["recommended"] = recommended_model(preset["models"] if preset else [cfg.default_model], out["models"])
    return out


# ---- aliases ---------------------------------------------------------------

@router.get("/aliases")
def list_aliases(db: Session = Depends(get_db)) -> List[dict]:
    config, _ = runtime.effective_config(db)
    panel = {row.alias for row in db.query(AliasOverride).all()}
    base = runtime.base_config().models
    return [
        {"alias": alias, "source": "panel" if alias in panel else "gateway.toml",
         "overrides_toml": alias in panel and alias in base,
         "targets": [spec for group in cfg.group_specs() for spec in group],
         "max_output_tokens": cfg.max_output_tokens, "cache_ttl_seconds": cfg.cache_ttl_seconds}
        for alias, cfg in config.models.items()
    ]


@router.put("/aliases/{alias}")
async def set_alias(
    alias: str, body: AliasIn, db: Session = Depends(get_db), services: GatewayServices = Depends(get_services)
) -> dict:
    if not re.match(NAME, alias):
        raise HTTPException(status_code=400, detail="alias: lowercase letters, digits, '-', '_', '.' (max 40)")
    config, _ = runtime.effective_config(db)
    if alias in config.providers or alias in config.deployments:
        raise HTTPException(status_code=409, detail=f"{alias!r} is already a provider or deployment name")
    row = db.get(AliasOverride, alias) or AliasOverride(alias=alias)
    row.targets = json.dumps(body.targets)
    row.max_output_tokens = body.max_output_tokens
    row.cache_ttl_seconds = body.cache_ttl_seconds
    db.add(row)
    _commit_and_apply(db, services)
    return {"alias": alias, "targets": body.targets}


@router.delete("/aliases/{alias}")
async def delete_alias(
    alias: str, db: Session = Depends(get_db), services: GatewayServices = Depends(get_services)
) -> dict:
    row = db.get(AliasOverride, alias)
    if row is None:
        raise HTTPException(status_code=404, detail="no panel alias with this name (gateway.toml aliases stay)")
    db.delete(row)
    _commit_and_apply(db, services)
    restored = alias in runtime.base_config().models
    return {"deleted": alias, "restored_from_toml": restored}


# ---- prices ----------------------------------------------------------------

@router.get("/prices")
def list_prices(db: Session = Depends(get_db)) -> List[dict]:
    config, _ = runtime.effective_config(db)
    panel = {row.key for row in db.query(PriceOverride).all()}
    return [{"key": key, "source": "panel" if key in panel else "gateway.toml", **price.model_dump()}
            for key, price in sorted(config.pricing.items())]


@router.put("/prices")
async def set_price(
    body: PriceIn, db: Session = Depends(get_db), services: GatewayServices = Depends(get_services)
) -> dict:
    row = db.get(PriceOverride, body.key) or PriceOverride(key=body.key)
    row.input_per_million = body.input_per_million
    row.output_per_million = body.output_per_million
    db.add(row)
    _commit_and_apply(db, services)
    return body.model_dump()


@router.delete("/prices")
async def delete_price(
    key: str = Query(...), db: Session = Depends(get_db), services: GatewayServices = Depends(get_services)
) -> dict:
    row = db.get(PriceOverride, key)
    if row is None:
        raise HTTPException(status_code=404, detail="no panel price for this key (gateway.toml prices stay)")
    db.delete(row)
    _commit_and_apply(db, services)
    return {"deleted": key}
