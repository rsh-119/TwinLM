"""Metrics (Prometheus), structured logging, and per-request context.

- Every HTTP request gets a server-generated request ID. An incoming
  `X-Request-ID` is echoed separately as a correlation hint, never used as
  the primary key of a usage record.
- Logs are JSON (or plain text via LOG_FORMAT=text). A redaction pass runs
  on every formatted line, so a credential that slips into a message or an
  exception never reaches the log sink.
"""
import contextvars
import json
import logging
import re
import time
import uuid
from typing import Optional

from prometheus_client import Counter, Gauge, Histogram
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# ---- metrics ---------------------------------------------------------------

REQUESTS = Counter(
    "llm_requests_total", "Gateway LLM requests", ["endpoint", "provider", "model", "status"]
)
REQUEST_LATENCY = Histogram(
    "llm_request_latency_seconds",
    "End-to-end gateway request latency",
    ["endpoint", "provider"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32, 64),
)
PROVIDER_LATENCY = Histogram(
    "llm_provider_latency_seconds",
    "Latency of individual provider attempts",
    ["provider"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32, 64),
)
TTFT = Histogram(
    "llm_time_to_first_token_seconds",
    "Time to first streamed token",
    ["provider"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16),
)
ERRORS = Counter("llm_request_errors_total", "Failed provider attempts", ["provider", "reason"])
TOKENS = Counter("llm_tokens_total", "Tokens processed", ["provider", "model", "type"])
COST = Counter("llm_cost_usd_total", "Estimated spend in USD", ["provider", "model"])
UNPRICED_CALLS = Counter(
    "llm_unpriced_calls_total", "Provider calls whose cost is unknown (no price or no usage)", ["provider"]
)
FALLBACKS = Counter("llm_fallback_total", "Requests served by a non-primary target", ["provider"])
RETRIES = Counter("llm_retries_total", "Same-provider retries", ["provider"])
CACHE_HITS = Counter("llm_cache_hits_total", "Response cache hits")
CACHE_MISSES = Counter("llm_cache_misses_total", "Response cache misses")
PROVIDER_HEALTH = Gauge(
    "llm_provider_health", "Circuit state: 1 closed, 0.5 half-open, 0 open", ["provider"]
)
ACTIVE_REQUESTS = Gauge("llm_active_requests", "In-flight gateway LLM requests")
USAGE_BACKLOG = Gauge("llm_usage_backlog_rows", "Usage rows awaiting database persistence")
USAGE_DEADLETTER = Gauge("llm_usage_deadletter_rows", "Usage rows retained after data errors")
USAGE_WRITE_FAILURES = Counter("llm_usage_write_failures_total", "Failed usage batch writes")
GUARDRAIL_VIOLATIONS = Counter(
    "llm_guardrail_violations_total", "Guardrail findings", ["guardrail", "stage", "action"]
)
STRUCTURED_OUTPUT_FAILURES = Counter(
    "llm_structured_output_failures_total", "Responses that failed JSON/schema validation"
)
BUDGET_ALERTS = Counter("llm_budget_alerts_total", "Budget threshold alerts sent", ["threshold"])
RATE_LIMITED = Counter("llm_rate_limited_total", "Requests rejected by rate limits", ["reason"])

# ---- request context -------------------------------------------------------

_request_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")
_QUIET_PATHS = {"/metrics", "/health", "/ready"}

access_logger = logging.getLogger("twinlm.access")


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def current_request_id() -> str:
    """The current HTTP request's ID; a fresh one outside a request."""
    return _request_id.get() or new_request_id()


class RequestContextMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so streaming responses and client
    disconnects pass through untouched."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        correlation_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else None
        request_id = new_request_id()
        token = _request_id.set(request_id)
        started = time.perf_counter()
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers.append("X-Request-ID", request_id)
                if correlation_id:
                    headers.append("X-Client-Request-ID", correlation_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            path = scope.get("path", "")
            access_logger.log(
                logging.DEBUG if path in _QUIET_PATHS else logging.INFO,
                "%s %s status=%d duration_ms=%d",
                scope.get("method", ""),
                path,
                status,
                int((time.perf_counter() - started) * 1000),
            )
            _request_id.reset(token)


# ---- logging ---------------------------------------------------------------

_REDACTIONS = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-~+/=]+"), r"\1[REDACTED]"),
    (re.compile(r"gw_live_[A-Za-z0-9_\-]+"), "gw_live_[REDACTED]"),
    (re.compile(r"(?i)([?&](?:key|api_key|token)=)[^&\s\"']+"), r"\1[REDACTED]"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"\b(?:sk|gsk|nvapi)-[A-Za-z0-9_\-]{16,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"(?i)(x-goog-api-key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,}]+"), r"\1[REDACTED]"),
]


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", None),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(entry, ensure_ascii=False))


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s [%(request_id)s] - %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(log_format: str = "json", level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if log_format == "json" else TextFormatter())
    handler.addFilter(_RequestIdFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # httpx logs every request URL at INFO; nothing there is worth the risk.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
