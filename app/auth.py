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
import math
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
from app.gateway.shared import get_redis

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
# Per process; with REDIS_URL a revocation is broadcast to every instance
# (a marker that outlives the cache TTL, checked on each cached hit).
_cache: Dict[str, Tuple[float, Principal]] = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 10_000
REVOKED_PREFIX = "twinlm:revoked:"


def forget_key(key_hash: str) -> None:
    """Drop a key from the auth cache (revocation, rotation) - on every
    instance when REDIS_URL is set."""
    with _cache_lock:
        _cache.pop(key_hash, None)
    redis = get_redis()
    if redis is not None:
        try:
            redis.set(f"{REVOKED_PREFIX}{key_hash}", 1, ex=math.ceil(get_settings().auth_cache_seconds) + 1)
        except Exception:  # noqa: BLE001 - other instances notice within AUTH_CACHE_SECONDS
            logger.warning("redis unavailable: revocation not broadcast", exc_info=True)


def _revoked_elsewhere(key_hash: str) -> bool:
    redis = get_redis()
    if redis is None:
        return False
    try:
        return bool(redis.exists(f"{REVOKED_PREFIX}{key_hash}"))
    except Exception:  # noqa: BLE001
        return False


def _lookup(db: Session, key_hash: str) -> Tuple[Optional[Principal], bool]:
    """(principal or None, revoked). Serves a fresh cache entry without the
    database, and an expired one only while the database is unreachable."""
    ttl = get_settings().auth_cache_seconds
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key_hash)
    if cached is not None and cached[0] > now and not _revoked_elsewhere(key_hash):
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
        console = _console_principal(request, db)
        if console is not None:
            return console
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


def _console_principal(request: Request, db: Session) -> Optional[Principal]:
    """The console playground: a signed-in user calls /v1 with their session
    cookie and `X-TwinLM-Team`. Usage is billed to that team and recorded
    under `console:<user id>` so it counts as the user's own usage."""
    from app.console_auth import COOKIE_NAME, check_csrf, session_user
    from app.database.models import Team

    team_name = request.headers.get("x-twinlm-team", "")
    token = request.cookies.get(COOKIE_NAME, "")
    if not team_name or not token:
        return None
    current = session_user(db, token)
    if current is None or current.user.must_change_password:
        raise _unauthorized("console session expired - sign in again")
    check_csrf(request, from_cookie=True)
    team = db.query(Team).filter(Team.name == team_name).first()
    if team is None or current.team_role(team.id) is None:
        raise HTTPException(status_code=403, detail=f"you are not in team {team_name!r}")
    return Principal(key_id=f"console:{current.user.id}", name=f"console:{current.user.email}", project=team.name,
                     allowed_models=None, rpm_limit=None, tpm_limit=None, daily_budget_usd=None,
                     user_id=current.user.id)


def require_admin(request: Request, db: Session = Depends(get_db)) -> None:
    """FastAPI dependency for /admin: the ADMIN_TOKEN, or the console session
    of a signed-in org admin. With neither ADMIN_TOKEN nor console users the
    admin API is disabled."""
    from app.console_auth import SESSION_PREFIX, check_csrf, session_token, session_user

    token, from_cookie = session_token(request)
    expected = get_settings().admin_token
    if not from_cookie and expected and hmac.compare_digest(token.encode(), expected.encode()):
        return
    if token.startswith(SESSION_PREFIX):
        check_csrf(request, from_cookie)
        current = session_user(db, token)
        if current is None:
            raise _unauthorized("console session expired - sign in again")
        if not current.is_admin or current.user.must_change_password:
            raise HTTPException(status_code=403, detail="only admins can do this")
        return
    if not expected:
        raise HTTPException(status_code=404, detail="admin API is disabled (set ADMIN_TOKEN)")
    raise _unauthorized("invalid admin token")
