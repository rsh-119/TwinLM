"""Gateway API-key authentication and the admin token check.

Keys look like `gw_live_<43 url-safe chars>` (256 bits of randomness), sent
as `Authorization: Bearer <key>` - exactly what the OpenAI SDK sends for
`api_key`. Only SHA-256(key) is stored: a fast hash is appropriate here
because the keys are high-entropy random secrets, not passwords.
"""
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from fastapi import Depends, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import repository
from app.database.database import get_db, release_connection
from app.database.models import ApiKey

logger = logging.getLogger("twinlm.auth")

KEY_PREFIX = "gw_live_"
# Project for unauthenticated callers (AUTH_ENABLED=false, the web UI).
DEFAULT_PROJECT = "default"


@dataclass(frozen=True)
class Principal:
    """The authenticated caller."""

    key_id: str
    name: str
    project: str
    allowed_models: Optional[List[str]]
    rpm_limit: Optional[int]
    tpm_limit: Optional[int]
    daily_budget_usd: Optional[float]
    # set = a user-bound key: conversations/memories limited to this user
    user_id: Optional[str] = None

    def may_use(self, model: str) -> bool:
        return self.allowed_models is None or model in self.allowed_models


def project_of(principal: Optional[Principal]) -> str:
    return principal.project if principal else DEFAULT_PROJECT


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_key() -> Tuple[str, str]:
    """(raw key, its hash)."""
    raw = KEY_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_key(raw)


def to_principal(key: ApiKey) -> Principal:
    return Principal(
        key_id=key.id,
        name=key.name,
        project=key.project,
        allowed_models=json.loads(key.allowed_models) if key.allowed_models else None,
        rpm_limit=key.rpm_limit,
        tpm_limit=key.tpm_limit,
        daily_budget_usd=key.daily_budget_usd,
        user_id=key.user_id,
    )


# key hash -> (fresh until, principal). Only valid keys are cached.
# ponytail: per-process cache; with several instances, revocation would
# need a shared invalidation signal (or a TTL-only guarantee).
_cache: Dict[str, Tuple[float, Principal]] = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 10_000


def forget_key(key_hash: str) -> None:
    """Drop a key from the auth cache (revocation, rotation)."""
    with _cache_lock:
        _cache.pop(key_hash, None)


def _lookup(db: Session, key_hash: str) -> Tuple[Optional[Principal], bool]:
    """(principal or None, revoked). Serves a fresh cache entry without the
    database, and an expired one only while the database is unreachable."""
    ttl = get_settings().auth_cache_seconds
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key_hash)
    if cached is not None and cached[0] > now:
        return cached[1], False
    try:
        key = repository.get_api_key_by_hash(db, key_hash)
    except SQLAlchemyError:
        if cached is not None:
            logger.warning("database unavailable; using last validated state for API key")
            return cached[1], False
        raise
    if key is None or key.revoked_at is not None:
        forget_key(key_hash)
        return None, key is not None
    principal = to_principal(key)
    if ttl > 0:
        with _cache_lock:
            if len(_cache) >= _CACHE_MAX:
                _cache.clear()
            _cache[key_hash] = (now + ttl, principal)
    return principal, False


def _bearer_token(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def authenticate(request: Request, db: Session = Depends(get_db)) -> Optional[Principal]:
    """FastAPI dependency. Returns None when auth is disabled
    (AUTH_ENABLED=false), so handlers treat "no principal" as "no limits"."""
    if not get_settings().auth_enabled:
        return None
    token = _bearer_token(request)
    if not token:
        raise _unauthorized("missing API key")
    try:
        principal, revoked = _lookup(db, hash_key(token))
    finally:
        # This dependency runs in a worker thread; don't let the session keep
        # its connection while the request waits to be scheduled on the loop.
        release_connection(db)
    if revoked:
        raise _unauthorized("API key has been revoked")
    if principal is None:
        raise _unauthorized("invalid API key")
    return principal


def require_admin(request: Request) -> None:
    """FastAPI dependency for /admin. ADMIN_TOKEN unset disables the admin API."""
    expected = get_settings().admin_token
    if not expected:
        raise HTTPException(status_code=404, detail="admin API is disabled (set ADMIN_TOKEN)")
    if not hmac.compare_digest(_bearer_token(request).encode(), expected.encode()):
        raise _unauthorized("invalid admin token")
