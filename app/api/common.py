"""Request-pipeline pieces shared by the chat and embeddings endpoints:
gateway services (DI), admission control, and provider-error mapping."""
import logging
import math
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Iterator, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth import Principal
from app.database import repository
from app.gateway.cache import ResponseCache, get_response_cache
from app.gateway.config import GatewayConfig, get_gateway_config
from app.gateway.guardrails import GuardrailEngine, get_guardrails
from app.gateway.rate_limit import SlidingWindowLimiter, get_rate_limiter
from app.database.database import SessionLocal
from app.gateway.usage import UsageRecord, UsageWriter
from app.config import get_settings
from app.observability import RATE_LIMITED
from app.providers.base import ProviderClientError, ProviderCredentialError, ProviderUnavailableError
from app.router.model_router import NoProviderAvailableError

logger = logging.getLogger("twinlm.api")


@dataclass(frozen=True)
class GatewayServices:
    config: GatewayConfig
    limiter: SlidingWindowLimiter
    cache: ResponseCache
    guardrails: GuardrailEngine
    # None = write request logs synchronously on the request's session
    usage_writer: Optional[UsageWriter] = None


@lru_cache
def get_services() -> GatewayServices:
    return GatewayServices(
        config=get_gateway_config(),
        limiter=get_rate_limiter(),
        cache=get_response_cache(),
        guardrails=get_guardrails(),
        usage_writer=UsageWriter(SessionLocal, spool_path=get_settings().usage_spool_path),
    )


def admit(
    model: str,
    principal: Optional[Principal],
    services: GatewayServices,
    db: Session,
    record: UsageRecord,
) -> None:
    """Model allow-list, rate limits and daily budget for the calling key.
    No principal (auth disabled) means no per-key limits."""
    if principal is None:
        return
    if not principal.may_use(model):
        record.fail(403, "model not allowed for this API key", "blocked")
        raise HTTPException(status_code=403, detail=f"model {model!r} is not allowed for this API key")

    defaults = services.config.rate_limits
    rejection = services.limiter.acquire(
        principal.key_id,
        principal.rpm_limit or defaults.default_rpm,
        principal.tpm_limit or defaults.default_tpm,
    )
    if rejection is not None:
        reason, retry_after = rejection
        RATE_LIMITED.labels(reason).inc()
        record.fail(429, f"rate limit exceeded: {reason}", "rate_limited")
        raise HTTPException(
            status_code=429,
            detail=f"rate limit exceeded ({reason})",
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )

    if principal.daily_budget_usd is not None:
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        settled = repository.sum_cost_since(db, principal.key_id, midnight)
        pending = services.usage_writer.pending_cost_since(principal.key_id, midnight) if services.usage_writer else 0.0
        if settled + pending >= principal.daily_budget_usd:
            RATE_LIMITED.labels("daily_budget").inc()
            record.fail(429, "daily budget exceeded", "rate_limited")
            raise HTTPException(status_code=429, detail="daily budget exceeded for this API key")


def unavailable_detail(exc: Exception) -> str:
    """Human-readable reason for a 503. `NoProviderAvailableError` wraps the
    *last* provider error in `__cause__` - surfacing it turns "all
    providers are currently unavailable" into something like "...: groq
    rate-limited this request: ... Limit 8000, Requested 8633", which
    actually tells you what to do next."""
    if isinstance(exc, NoProviderAvailableError) and exc.__cause__:
        return f"all providers are currently unavailable: {exc.__cause__}"
    if isinstance(exc, NoProviderAvailableError):
        return f"all providers are currently unavailable: {exc}"
    return f"provider unavailable: {exc}"


@contextmanager
def provider_errors(record: UsageRecord) -> Iterator[None]:
    """Maps router/provider exceptions to HTTP errors: unknown model or a
    provider's client error -> 400, unavailable -> 503, anything else ->
    502 (never a bare 500 for an upstream problem)."""
    try:
        yield
    except HTTPException:
        raise
    except ValueError as exc:
        record.fail(400, str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    except ProviderClientError as exc:
        record.fail(400, str(exc))
        raise HTTPException(status_code=400, detail=str(exc))
    except ProviderCredentialError as exc:
        record.fail(503, str(exc))
        logger.error("provider credential failure: model=%s error=%s", record.requested_model, exc)
        raise HTTPException(status_code=503, detail="provider credentials are unavailable")
    except TimeoutError:
        record.fail(503, "request deadline exceeded")
        raise HTTPException(status_code=503, detail="request deadline exceeded")
    except (ProviderUnavailableError, NoProviderAvailableError) as exc:
        detail = unavailable_detail(exc)
        record.fail(503, detail)
        logger.error("request failed: model=%s status=unavailable error=%s", record.requested_model, detail)
        raise HTTPException(status_code=503, detail=detail)
    except Exception as exc:  # noqa: BLE001
        record.fail(502, f"upstream provider error: {type(exc).__name__}")
        logger.exception("request failed: model=%s status=upstream_error", record.requested_model)
        raise HTTPException(status_code=502, detail="upstream provider error")
