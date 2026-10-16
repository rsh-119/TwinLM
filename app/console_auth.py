"""Console sign-in: users with passwords, session tokens and roles.

Org roles: `admin` (everything, including the existing /admin API) and
`member`. Team roles (team_members.role): `admin` (manages the team's
members and keys) and `member`. Passwords use stdlib scrypt; session tokens,
like API keys, are random and stored only as a SHA-256 hash."""
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.database.models import TeamMember, User, UserSession

SESSION_PREFIX = "tws_"
SESSION_HOURS = 12
COOKIE_NAME = "twinlm_session"
# Cookie-authenticated writes must carry this header. Browsers don't let
# other sites add custom headers to cross-site requests, so this (with
# SameSite=Strict) blocks CSRF.
CSRF_HEADER = "x-twinlm-console"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # DB stores naive UTC


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), dklen=32, **_SCRYPT)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate.hex(), digest)


def temporary_password() -> str:
    return secrets.token_urlsafe(9)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User) -> str:
    token = SESSION_PREFIX + secrets.token_urlsafe(32)
    db.add(UserSession(token_hash=_token_hash(token), user_id=user.id,
                       expires_at=_now() + timedelta(hours=SESSION_HOURS)))
    user.last_login_at = _now()
    db.commit()
    return token


def end_session(db: Session, token: str) -> None:
    db.query(UserSession).filter(UserSession.token_hash == _token_hash(token)).delete()
    db.commit()


@dataclass
class ConsoleUser:
    user: User
    token: str
    teams: Dict[str, str] = field(default_factory=dict)  # team_id -> team role

    @property
    def is_admin(self) -> bool:
        return self.user.role == "admin"

    def team_role(self, team_id: str) -> Optional[str]:
        """Effective role in a team; org admins act as team admins everywhere."""
        return "admin" if self.is_admin else self.teams.get(team_id)


def _bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def session_token(request: Request) -> Tuple[str, bool]:
    """(token, came from the cookie). A Bearer header wins over the cookie."""
    bearer = _bearer(request)
    if bearer:
        return bearer, False
    return request.cookies.get(COOKIE_NAME, ""), True


def check_csrf(request: Request, from_cookie: bool) -> None:
    if from_cookie and request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(status_code=403, detail="missing X-TwinLM-Console header")


def set_session_cookie(response, request: Request, token: str) -> None:
    secure = request.url.scheme == "https"
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_HOURS * 3600, httponly=True,
                        secure=secure, samesite="strict", path="/")


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def session_user(db: Session, token: str) -> Optional[ConsoleUser]:
    if not token.startswith(SESSION_PREFIX):
        return None
    row = db.get(UserSession, _token_hash(token))
    if row is None or row.expires_at < _now():
        return None
    user = db.get(User, row.user_id)
    if user is None or user.disabled_at is not None:
        return None
    teams = {m.team_id: m.role for m in db.query(TeamMember).filter(TeamMember.user_id == user.id)}
    return ConsoleUser(user=user, token=token, teams=teams)


def console_user(request: Request, db: Session = Depends(get_db)) -> ConsoleUser:
    """FastAPI dependency: the signed-in console user (401 otherwise), from a
    Bearer session token or the session cookie."""
    token, from_cookie = session_token(request)
    current = session_user(db, token)
    if current is not None:
        check_csrf(request, from_cookie)
    if current is None:
        raise HTTPException(status_code=401, detail="sign in to the console first",
                            headers={"WWW-Authenticate": "Bearer"})
    return current


def ready_user(current: ConsoleUser = Depends(console_user)) -> ConsoleUser:
    """A signed-in user who has already replaced their temporary password."""
    if current.user.must_change_password:
        raise HTTPException(status_code=403, detail="set a new password first")
    return current
