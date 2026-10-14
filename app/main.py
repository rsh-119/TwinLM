"""FastAPI application entrypoint for TwinLM."""
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import admin, chat, conversations, embeddings, memories, models
from app.api.common import get_services
from app.config import get_settings
from app.database.database import get_db, init_db
from app.gateway.config import get_gateway_config
from app.gateway.guardrails import get_guardrails
from app.observability import RequestContextMiddleware, setup_logging
from app.request_limits import BodyLimitMiddleware, ConcurrencyLimitMiddleware
from app.router.model_router import get_model_router
from app.web import routes as web_routes

settings = get_settings()
setup_logging(settings.log_format, settings.log_level)
logger = logging.getLogger("twinlm")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    _app.state.ready = False
    if settings.auth_enabled and not settings.admin_token:
        raise RuntimeError(
            "AUTH_ENABLED=true but ADMIN_TOKEN is empty: no API key could ever be created. "
            "Set ADMIN_TOKEN, or AUTH_ENABLED=false for a private single-user setup."
        )
    get_gateway_config()
    get_guardrails()
    get_model_router()
    get_services()
    applied = init_db(apply=settings.migrate_on_startup)
    if applied:
        logger.info("database migrations applied: %s", applied)
    _app.state.ready = True
    logger.info(
        "TwinLM started: auth_enabled=%s admin_api=%s web_ui=%s stateful=%s",
        settings.auth_enabled, bool(settings.admin_token), settings.web_ui_enabled, settings.stateful_enabled,
    )
    try:
        yield
    finally:
        _app.state.ready = False
        if get_model_router.cache_info().currsize:
            await get_model_router().aclose()
        if get_services.cache_info().currsize and get_services().usage_writer:
            get_services().usage_writer.close()  # flush queued request logs
        get_model_router.cache_clear()
        get_services.cache_clear()


app = FastAPI(
    title="TwinLM",
    description="An OpenAI-compatible LLM gateway with multi-provider routing and shared conversation memory.",
    version="0.2.0",
    lifespan=lifespan,
)
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(ConcurrencyLimitMiddleware, max_requests=settings.max_concurrent_requests)
app.add_middleware(RequestContextMiddleware)

_ERROR_TYPES = {
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    429: "rate_limit_error",
}


def _error_response(status: int, message: str, detail, headers=None, code=None, param=None) -> JSONResponse:
    """OpenAI's error shape, so SDKs surface `message` instead of a generic
    error. `detail` is kept for clients written against FastAPI's shape."""
    error_type = _ERROR_TYPES.get(status, "api_error" if status >= 500 else "invalid_request_error")
    body = {"error": {"message": message, "type": error_type, "param": param, "code": code}, "detail": detail}
    return JSONResponse(body, status_code=status, headers=headers)


@app.exception_handler(StarletteHTTPException)
async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return _error_response(exc.status_code, str(exc.detail), exc.detail, exc.headers)


@app.exception_handler(OperationalError)
@app.exception_handler(PoolTimeoutError)
async def database_unavailable(_request: Request, exc: Exception) -> JSONResponse:
    """A database outage is a retryable 503, not an opaque 500."""
    logger.error("database unavailable: %s", type(exc).__name__)
    return _error_response(503, "database unavailable", "database unavailable", {"Retry-After": "5"})


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = jsonable_encoder(exc.errors())
    unsupported = [".".join(map(str, e["loc"][1:])) for e in errors if e["type"] == "extra_forbidden"]
    if unsupported:
        # OpenAI answers unknown parameters with 400, not 422
        return _error_response(
            400, f"unsupported parameter: {', '.join(unsupported)}", errors,
            code="unsupported_parameter", param=unsupported[0],
        )
    message = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in errors)
    return _error_response(422, message, errors)

app.include_router(chat.router)
app.include_router(embeddings.router)
app.include_router(models.router)
if settings.stateful_enabled:
    app.include_router(conversations.router)
    app.include_router(memories.router)
app.include_router(admin.router)
if settings.web_ui_enabled and settings.stateful_enabled:
    app.include_router(web_routes.router)


@app.get("/health")
async def health() -> dict:
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/ready")
def ready(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Readiness: the database is reachable. Providers aren't checked - a
    provider outage is handled by fallback, not by pulling the instance."""
    if not getattr(request.app.state, "ready", False):
        return JSONResponse({"status": "unavailable"}, status_code=503)
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("readiness check failed: database unreachable")
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ready"})


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """Prometheus scrape endpoint. Unauthenticated, like most exporters -
    keep it on an internal network."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
