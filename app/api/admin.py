"""Admin / control-plane API (Bearer ADMIN_TOKEN; disabled when unset):
provider health, model registry, usage & cost, API keys, cache."""
import json
import statistics
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.common import GatewayServices, get_services
from app.auth import DEFAULT_PROJECT, forget_key, generate_key, require_admin
from app.database import repository
from app.database.database import get_db
from app.database.models import Budget, BudgetAlert, User
from app.gateway import budgets
from app.gateway.pricing import request_cost
from app.providers.base import TokenUsage
from app.router.model_router import ModelRouter, get_model_router

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/providers")
def providers(model_router: ModelRouter = Depends(get_model_router)) -> List[dict]:
    """Circuit state, error rate, latency and load per provider (this instance)."""
    return model_router.health_snapshot()


@router.get("/models")
def models(model_router: ModelRouter = Depends(get_model_router)) -> List[dict]:
    """Aliases and providers with their resolved targets and pricing."""
    return model_router.describe_models()


@router.get("/usage")
def usage(
    hours: float = Query(default=24, gt=0, le=24 * 90),
    group_by: Literal["provider", "model", "deployment", "project", "api_key_id"] = "provider",
    db: Session = Depends(get_db),
) -> dict:
    """Requests, errors, fallbacks, cache hits, tokens, cost and latency
    over the last `hours`, grouped, plus a total row. Costs are estimates
    from configured prices; `cost_complete` is false when some requests
    have unknown cost (reconcile against provider invoices)."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    groups = repository.usage_summary(db, since, group_by)
    total_requests = sum(g["requests"] for g in groups)
    costs = [g["cost_usd"] for g in groups if g["cost_usd"] is not None]
    for group in groups:
        if group_by in ("provider", "model") and group.get("provider"):
            latencies = repository.latencies_since(
                db, since, group["provider"], group.get("model") if group_by == "model" else None
            )
            group["p50_latency_ms"], group["p95_latency_ms"] = _percentiles(latencies)
    return {
        "since": since.isoformat(),
        "group_by": group_by,
        "total": {
            "requests": total_requests,
            "errors": sum(g["errors"] for g in groups),
            "fallbacks": sum(g["fallbacks"] for g in groups),
            "cache_hits": sum(g["cache_hits"] for g in groups),
            "cache_hit_rate": round(sum(g["cache_hits"] for g in groups) / total_requests, 4) if total_requests else 0.0,
            "total_tokens": sum(g["total_tokens"] for g in groups),
            "cost_usd": round(sum(costs), 8) if costs else None,
            "unpriced_requests": sum(g["unpriced_requests"] for g in groups),
            "cost_complete": not any(g["unpriced_requests"] for g in groups),
        },
        "groups": groups,
    }


@router.get("/usage/daily")
def usage_daily(days: int = Query(default=30, ge=1, le=366), db: Session = Depends(get_db)) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return {"since": since.isoformat(), "days": repository.daily_usage(db, since)}


@router.get("/savings")
def savings(
    hours: float = Query(default=24 * 30, gt=0, le=24 * 366),
    group_by: Literal["project", "api_key_id", "provider", "deployment"] = "project",
    db: Session = Depends(get_db),
    services: GatewayServices = Depends(get_services),
) -> dict:
    """What the measured traffic cost vs. what the same tokens would have
    cost on `[savings] baseline` - the model the business would otherwise
    send everything to. The difference comes from cheaper routing and cache
    hits; `cache_avoided_usd` is what cache hits would have cost on the
    model that originally answered. Estimates: token counts are the served
    models' own, and requests with unknown tokens or price are not measured
    (`measured_requests` < `requests` means the figures are partial)."""
    baseline = services.config.savings.baseline
    if not baseline:
        raise HTTPException(status_code=404, detail="set [savings] baseline in gateway.toml")
    price = services.config.pricing[baseline]
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    groups: dict = {}
    for key, provider, model, cache_hit, requests, measured, prompt, completion, cost in repository.savings_rows(
        db, since, group_by
    ):
        group = groups.setdefault(key, dict.fromkeys(
            ("requests", "measured_requests", "cache_hits", "actual_cost_usd", "baseline_cost_usd", "cache_avoided_usd"), 0
        ))
        group["requests"] += requests
        if cache_hit:
            group["cache_hits"] += requests
            avoided = request_cost(services.config.pricing, provider, model, TokenUsage(prompt, completion, prompt + completion))
            if avoided is None:
                measured = 0  # the answering model is unpriced: its cache hits can't be valued
            else:
                group["cache_avoided_usd"] += avoided
        else:
            group["actual_cost_usd"] += cost or 0.0
        if measured:
            group["measured_requests"] += measured
            group["baseline_cost_usd"] += (prompt * price.input_per_million + completion * price.output_per_million) / 1e6

    def finish_row(row: dict) -> dict:
        row["savings_usd"] = row["baseline_cost_usd"] - row["actual_cost_usd"]
        row["savings_pct"] = round(100 * row["savings_usd"] / row["baseline_cost_usd"], 1) if row["baseline_cost_usd"] else None
        row["complete"] = row["measured_requests"] == row["requests"]
        return {k: round(v, 6) if isinstance(v, float) else v for k, v in row.items()}

    total = {k: sum(g[k] for g in groups.values()) for k in
             ("requests", "measured_requests", "cache_hits", "actual_cost_usd", "baseline_cost_usd", "cache_avoided_usd")}
    return {
        "since": since.isoformat(),
        "baseline": baseline,
        "group_by": group_by,
        "total": finish_row(total),
        "groups": [{group_by: key, **finish_row(row)} for key, row in groups.items()],
    }


def _percentiles(values: List[int]):
    # ponytail: computed in Python over the window's rows - fine for
    # thousands of requests; use Prometheus histograms for more.
    if len(values) < 2:
        return (values[0], values[0]) if values else (None, None)
    q = statistics.quantiles(values, n=100, method="inclusive")
    return round(q[49], 1), round(q[94], 1)


class CreateKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    project: str = Field(min_length=1, max_length=100)
    allowed_models: Optional[List[str]] = None
    rpm_limit: Optional[int] = Field(default=None, gt=0)
    tpm_limit: Optional[int] = Field(default=None, gt=0)
    daily_budget_usd: Optional[float] = Field(default=None, ge=0)
    # set = user-bound key (a person); unset = service key (an application)
    user_id: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @field_validator("project")
    @classmethod
    def _not_reserved(cls, project: str) -> str:
        # unauthenticated callers and the web UI live there
        if project == DEFAULT_PROJECT:
            raise ValueError(f"project {DEFAULT_PROJECT!r} is reserved for unauthenticated access")
        return project


def _key_view(key) -> dict:
    return {
        "id": key.id,
        "name": key.name,
        "project": key.project,
        "key_prefix": key.key_prefix,
        "allowed_models": json.loads(key.allowed_models) if key.allowed_models else None,
        "rpm_limit": key.rpm_limit,
        "tpm_limit": key.tpm_limit,
        "daily_budget_usd": key.daily_budget_usd,
        "user_id": key.user_id,
        "created_at": key.created_at.isoformat() if key.created_at else None,
        "revoked_at": key.revoked_at.isoformat() if key.revoked_at else None,
    }


@router.post("/keys", status_code=201)
def create_key(body: CreateKeyRequest, db: Session = Depends(get_db)) -> dict:
    """Returns the raw key once. Only its hash is stored."""
    raw, key_hash = generate_key()
    key = repository.create_api_key(
        db,
        name=body.name,
        project=body.project,
        key_hash=key_hash,
        key_prefix=raw[:14],
        allowed_models=json.dumps(body.allowed_models) if body.allowed_models is not None else None,
        rpm_limit=body.rpm_limit,
        tpm_limit=body.tpm_limit,
        daily_budget_usd=body.daily_budget_usd,
        user_id=body.user_id,
    )
    return {**_key_view(key), "key": raw}


@router.post("/keys/{key_id}/rotate", status_code=201)
def rotate_key(key_id: str, revoke_old: bool = False, db: Session = Depends(get_db)) -> dict:
    """Issue a new key with the same project, user, limits and budget.
    The old key keeps working until revoked (pass `revoke_old=true` to
    revoke it now) so clients can switch over without downtime."""
    old = repository.get_api_key(db, key_id)
    if old is None or old.revoked_at is not None:
        raise HTTPException(status_code=404, detail="active API key not found")
    raw, key_hash = generate_key()
    key = repository.create_api_key(
        db, name=old.name, project=old.project, key_hash=key_hash, key_prefix=raw[:14],
        allowed_models=old.allowed_models, rpm_limit=old.rpm_limit, tpm_limit=old.tpm_limit,
        daily_budget_usd=old.daily_budget_usd, user_id=old.user_id,
    )
    if revoke_old:
        repository.revoke_api_key(db, key_id)
        forget_key(old.key_hash)
    return {**_key_view(key), "key": raw, "replaces": key_id}


@router.get("/keys")
def list_keys(db: Session = Depends(get_db)) -> List[dict]:
    return [_key_view(k) for k in repository.list_api_keys(db)]


@router.delete("/keys/{key_id}")
def revoke_key(key_id: str, db: Session = Depends(get_db)) -> dict:
    key = repository.revoke_api_key(db, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="API key not found")
    forget_key(key.key_hash)
    return _key_view(key)


class BudgetIn(BaseModel):
    scope: Literal["org", "team", "user"]
    # team name (= project), user id; ignored for "org"
    target: str = Field(default="*", min_length=1, max_length=200)
    period: Literal["daily", "monthly"] = "monthly"
    limit_usd: float = Field(gt=0)


def _budget_view(db: Session, budget: Budget, services: GatewayServices) -> dict:
    view = budgets.status(db, budget, services.budgets, services.usage_writer)
    if budget.scope == "user":
        user = db.get(User, budget.target)
        view["target_name"] = user.name if user else None
    return view


@router.get("/budgets")
def list_budgets(db: Session = Depends(get_db), services: GatewayServices = Depends(get_services)) -> List[dict]:
    """Every budget with its spend in the current period."""
    rows = db.query(Budget).order_by(Budget.scope, Budget.target, Budget.period).all()
    return [_budget_view(db, b, services) for b in rows]


@router.put("/budgets")
def set_budget(body: BudgetIn, db: Session = Depends(get_db),
               services: GatewayServices = Depends(get_services)) -> dict:
    """Create or change the budget for (scope, target, period). Applies to
    the next request."""
    target = "*" if body.scope == "org" else body.target
    budget = db.query(Budget).filter(Budget.scope == body.scope, Budget.target == target,
                                     Budget.period == body.period).first()
    if budget is None:
        budget = Budget(scope=body.scope, target=target, period=body.period, limit_usd=body.limit_usd)
        db.add(budget)
    budget.limit_usd = body.limit_usd
    db.commit()
    return _budget_view(db, budget, services)


@router.delete("/budgets/{budget_id}")
def delete_budget(budget_id: str, db: Session = Depends(get_db)) -> dict:
    budget = db.get(Budget, budget_id)
    if budget is None:
        raise HTTPException(status_code=404, detail="budget not found")
    db.query(BudgetAlert).filter(BudgetAlert.budget_id == budget_id).delete()
    db.delete(budget)
    db.commit()
    return {"deleted": budget_id}


@router.get("/budget-alerts")
def budget_alerts(limit: int = Query(default=50, ge=1, le=500), db: Session = Depends(get_db)) -> List[dict]:
    """Thresholds crossed (80%, 100%), newest first."""
    rows = (db.query(BudgetAlert, Budget).join(Budget, Budget.id == BudgetAlert.budget_id)
            .order_by(BudgetAlert.created_at.desc()).limit(limit).all())
    return [{"id": a.id, "budget_id": b.id, "budget": budgets.describe(b), "threshold_pct": a.threshold,
             "spent_usd": a.spent_usd, "limit_usd": a.limit_usd, "period_start": a.period_start.isoformat(),
             "created_at": a.created_at.isoformat()} for a, b in rows]


@router.delete("/cache")
def clear_cache(services: GatewayServices = Depends(get_services)) -> dict:
    semantic = services.semantic_cache.clear() if services.semantic_cache is not None else 0
    return {"cleared": services.cache.clear() + semantic}
