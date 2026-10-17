"""Console sign-in, people, teams, members and team keys.

/auth/*      sign in, sign out, who am I, change password
/admin/...   org admins (or ADMIN_TOKEN): people and teams
/console/... signed-in users, scoped by team role:
             team admins manage their team's members and keys,
             members manage only their own keys and see their own usage.
A team's name is the `project` of its keys, so usage, savings and cache
isolation per project are per team."""
import json
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

import hmac

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.auth import DEFAULT_PROJECT, forget_key, generate_key, require_admin
from app.config import get_settings
from app.console_auth import (
    ConsoleUser, clear_session_cookie, console_user, create_session, end_session, hash_password, ready_user,
    set_session_cookie, temporary_password, verify_password,
)
from app.database import repository
from app.database.database import get_db
from app.database.models import ApiKey, Budget, BudgetAlert, RequestLog, Team, TeamMember, User
from app.api.common import GatewayServices, get_services
from app.gateway import budgets
from app.gateway.rate_limit import get_rate_limiter

auth_router = APIRouter(prefix="/auth", tags=["console"])
admin_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
console_router = APIRouter(prefix="/console", tags=["console"])

TEAM_NAME = r"^[a-z0-9][a-z0-9_.-]{0,39}$"
LOGIN_ATTEMPTS_PER_MINUTE = 10
SIGNUPS_PER_MINUTE_PER_IP = 3


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _user_view(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role,
            "must_change_password": user.must_change_password, "created_at": _iso(user.created_at),
            "last_login_at": _iso(user.last_login_at), "disabled": user.disabled_at is not None}


def _key_view(key: ApiKey, users: Optional[dict] = None) -> dict:
    owner = (users or {}).get(key.user_id)
    return {"id": key.id, "name": key.name, "team": key.project, "key_prefix": key.key_prefix,
            "user_id": key.user_id, "owner": owner.name if owner else None,
            "kind": "personal" if key.user_id else "service",
            "allowed_models": json.loads(key.allowed_models) if key.allowed_models else None,
            "rpm_limit": key.rpm_limit, "tpm_limit": key.tpm_limit, "daily_budget_usd": key.daily_budget_usd,
            "created_at": _iso(key.created_at), "revoked_at": _iso(key.revoked_at)}


def _last_used(db: Session, key_ids: List[str]) -> dict:
    """key id -> time of its latest request (computed on read; nothing is
    written per request for this)."""
    if not key_ids:
        return {}
    rows = (db.query(RequestLog.api_key_id, func.max(RequestLog.created_at))
            .filter(RequestLog.api_key_id.in_(key_ids)).group_by(RequestLog.api_key_id).all())
    return dict(rows)


def _latest(*values: Optional[datetime]) -> Optional[datetime]:
    present = [v.replace(tzinfo=None) if v and v.tzinfo else v for v in values if v]
    return max(present) if present else None


def _admins_left(db: Session, excluding: str) -> int:
    return db.query(User).filter(User.role == "admin", User.disabled_at.is_(None), User.id != excluding).count()


# ---- /auth -----------------------------------------------------------------

class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)


@auth_router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    """Returns a session token (for scripts) and sets it as an httpOnly cookie
    (for the web console)."""
    email = body.email.strip().lower()
    limiter = get_rate_limiter()
    client = request.client.host if request.client else "?"
    for bucket in (f"login:{email}", f"login-ip:{client}"):
        if limiter.acquire(bucket, LOGIN_ATTEMPTS_PER_MINUTE, None):
            raise HTTPException(status_code=429, detail="too many sign-in attempts - wait a minute",
                                headers={"Retry-After": "60"})
    user = db.query(User).filter(User.email == email).first()
    if user is None or user.disabled_at is not None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="wrong email or password")
    token = create_session(db, user)
    set_session_cookie(response, request, token)
    return {"token": token, "user": _user_view(user)}


@auth_router.post("/logout")
def logout(response: Response, current: ConsoleUser = Depends(console_user), db: Session = Depends(get_db)) -> dict:
    end_session(db, current.token)
    clear_session_cookie(response)
    return {"signed_out": True}


class SetupIn(BaseModel):
    admin_token: str
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$", max_length=200)
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=200)


@auth_router.get("/setup")
def setup_status(db: Session = Depends(get_db)) -> dict:
    """First run: nobody can sign in until the first admin exists. Also
    whether visitors may create an account (hosted beta)."""
    settings = get_settings()
    users = db.query(User).count()
    return {"needs_setup": users == 0, "signup_enabled": settings.signup_enabled and users > 0,
            "signup_full": users >= settings.signup_max_accounts}


class SignupIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=200)
    team: str = Field(pattern=TEAM_NAME)


def _beta_full(db: Session) -> bool:
    """With sign-up on, the account cap covers every console user."""
    settings = get_settings()
    return settings.signup_enabled and db.query(User).count() >= settings.signup_max_accounts


@auth_router.post("/signup", status_code=201)
def signup(body: SignupIn, request: Request, response: Response, db: Session = Depends(get_db),
           services: GatewayServices = Depends(get_services)) -> dict:
    """Hosted beta: create an account, a team the new user administers and
    the team's monthly budget, then sign them in. Opens only after the first
    admin exists, so the operator always holds the admin account."""
    settings = get_settings()
    if not settings.signup_enabled or not db.query(User).count():
        raise HTTPException(status_code=404, detail="sign-up is not open")
    client = request.client.host if request.client else "?"
    if get_rate_limiter().acquire(f"signup-ip:{client}", SIGNUPS_PER_MINUTE_PER_IP, None):
        raise HTTPException(status_code=429, detail="too many sign-ups from here - wait a minute",
                            headers={"Retry-After": "60"})
    if _beta_full(db):
        raise HTTPException(status_code=403, detail="the beta is full - try again later")
    email = body.email.strip().lower()
    if body.team == DEFAULT_PROJECT:
        raise HTTPException(status_code=400, detail=f"{DEFAULT_PROJECT!r} is reserved")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="an account with this email exists - sign in instead")
    # a project used by admin-created keys counts as taken too: its usage isn't the newcomer's
    if db.query(Team).filter(Team.name == body.team).first() or \
            db.query(ApiKey).filter(ApiKey.project == body.team).first():
        raise HTTPException(status_code=409, detail="this team name is taken - choose another")
    user = User(email=email, name=body.name.strip(), role="member", password_hash=hash_password(body.password),
                must_change_password=False)
    team = Team(name=body.team)
    db.add_all([user, team])
    db.flush()
    db.add_all([TeamMember(team_id=team.id, user_id=user.id, role="admin"),
                Budget(scope="team", target=team.name, period="monthly", limit_usd=settings.signup_team_budget_usd)])
    db.commit()
    services.budgets.invalidate()
    token = create_session(db, user)
    set_session_cookie(response, request, token)
    return {"token": token, "user": _user_view(user), "team": {"id": team.id, "name": team.name}}


@auth_router.post("/setup")
def setup(body: SetupIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    """Create the first admin (only while there are no users), proven by the
    gateway's ADMIN_TOKEN, and sign them in."""
    expected = get_settings().admin_token
    if not expected or not hmac.compare_digest(body.admin_token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="wrong admin token")
    if db.query(User).count():
        raise HTTPException(status_code=409, detail="TwinLM is already set up - sign in instead")
    user = User(email=body.email.strip().lower(), name=body.name.strip(), role="admin",
                password_hash=hash_password(body.password), must_change_password=False)
    db.add(user)
    db.commit()
    token = create_session(db, user)
    set_session_cookie(response, request, token)
    return {"token": token, "user": _user_view(user)}


@auth_router.get("/me")
def me(current: ConsoleUser = Depends(console_user), db: Session = Depends(get_db)) -> dict:
    teams = {t.id: t for t in db.query(Team).filter(Team.id.in_(list(current.teams)))} if current.teams else {}
    return {**_user_view(current.user),
            "teams": [{"id": tid, "name": teams[tid].name, "role": role}
                      for tid, role in current.teams.items() if tid in teams]}


@auth_router.post("/password")
def change_password(body: PasswordIn, current: ConsoleUser = Depends(console_user),
                    db: Session = Depends(get_db)) -> dict:
    user = db.get(User, current.user.id)
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="current password is wrong")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    db.commit()
    return {"changed": True}


# ---- /admin: people and teams ----------------------------------------------

class UserIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$", max_length=200)
    name: str = Field(min_length=1, max_length=100)
    role: Literal["admin", "member"] = "member"


class UserPatch(BaseModel):
    role: Optional[Literal["admin", "member"]] = None
    disabled: Optional[bool] = None
    reset_password: bool = False


class TeamIn(BaseModel):
    name: str = Field(pattern=TEAM_NAME)


@admin_router.get("/users")
def list_users(db: Session = Depends(get_db)) -> List[dict]:
    memberships = db.query(TeamMember, Team).join(Team, Team.id == TeamMember.team_id).all()
    teams_of: dict = {}
    for member, team in memberships:
        teams_of.setdefault(member.user_id, []).append({"id": team.id, "name": team.name, "role": member.role})
    return [{**_user_view(u), "teams": teams_of.get(u.id, [])}
            for u in db.query(User).order_by(User.created_at).all()]


@admin_router.post("/users", status_code=201)
def create_user(body: UserIn, db: Session = Depends(get_db)) -> dict:
    """Returns a temporary password once; the user sets their own at first sign-in.
    With ADMIN_TOKEN this also bootstraps the first admin."""
    email = body.email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="a user with this email already exists")
    password = temporary_password()
    user = User(email=email, name=body.name.strip(), role=body.role, password_hash=hash_password(password),
                must_change_password=True)
    db.add(user)
    db.commit()
    return {**_user_view(user), "temporary_password": password}


@admin_router.patch("/users/{user_id}")
def update_user(user_id: str, body: UserPatch, db: Session = Depends(get_db)) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    losing_admin = user.role == "admin" and (body.role == "member" or body.disabled)
    if losing_admin and _admins_left(db, user.id) == 0:
        raise HTTPException(status_code=409, detail="keep at least one active admin")
    if body.role:
        user.role = body.role
    if body.disabled is not None:
        user.disabled_at = _now() if body.disabled else None
    out: dict = {}
    if body.reset_password:
        out["temporary_password"] = temporary_password()
        user.password_hash = hash_password(out["temporary_password"])
        user.must_change_password = True
    db.commit()
    return {**_user_view(user), **out}


@admin_router.post("/teams", status_code=201)
def create_team(body: TeamIn, db: Session = Depends(get_db)) -> dict:
    if body.name == DEFAULT_PROJECT:
        raise HTTPException(status_code=400, detail=f"{DEFAULT_PROJECT!r} is reserved")
    if db.query(Team).filter(Team.name == body.name).first():
        raise HTTPException(status_code=409, detail="a team with this name already exists")
    team = Team(name=body.name)
    db.add(team)
    db.commit()
    return {"id": team.id, "name": team.name}


@admin_router.delete("/teams/{team_id}")
def delete_team(team_id: str, db: Session = Depends(get_db)) -> dict:
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="team not found")
    active = db.query(ApiKey).filter(ApiKey.project == team.name, ApiKey.revoked_at.is_(None)).count()
    if active:
        raise HTTPException(status_code=409, detail=f"revoke the team's {active} active key(s) first")
    db.query(TeamMember).filter(TeamMember.team_id == team_id).delete()
    for budget in db.query(Budget).filter(Budget.scope == "team", Budget.target == team.name):
        db.query(BudgetAlert).filter(BudgetAlert.budget_id == budget.id).delete()
        db.delete(budget)
    db.delete(team)
    db.commit()
    return {"deleted": team.name}


# ---- /console: team-scoped -------------------------------------------------

class MemberIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$", max_length=200)
    name: Optional[str] = Field(default=None, max_length=100)
    role: Literal["admin", "member"] = "member"


class MemberPatch(BaseModel):
    role: Literal["admin", "member"]


class TeamKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    # "me" = personal key of the caller; a user id = that member's key
    # (team admins); null = service key for an application (team admins)
    for_user: Optional[str] = "me"
    allowed_models: Optional[List[str]] = None
    rpm_limit: Optional[int] = Field(default=None, gt=0)
    tpm_limit: Optional[int] = Field(default=None, gt=0)
    daily_budget_usd: Optional[float] = Field(default=None, ge=0)


def _team(db: Session, team_id: str, current: ConsoleUser, need: str = "member") -> Team:
    team = db.get(Team, team_id)
    role = current.team_role(team_id)
    if team is None or role is None:
        raise HTTPException(status_code=404, detail="team not found")
    if need == "admin" and role != "admin":
        raise HTTPException(status_code=403, detail="only team admins can do this")
    return team


@console_router.get("/teams")
def my_teams(current: ConsoleUser = Depends(ready_user), db: Session = Depends(get_db)) -> List[dict]:
    query = db.query(Team).order_by(Team.name)
    teams = query.all() if current.is_admin else query.filter(Team.id.in_(list(current.teams))).all()
    since = _now() - timedelta(days=30)
    out = []
    for team in teams:
        members = db.query(TeamMember).filter(TeamMember.team_id == team.id).count()
        keys = db.query(ApiKey).filter(ApiKey.project == team.name, ApiKey.revoked_at.is_(None)).count()
        spend = (db.query(func.sum(RequestLog.cost_usd))
                 .filter(RequestLog.project == team.name, RequestLog.created_at >= since).scalar())
        out.append({"id": team.id, "name": team.name, "my_role": current.team_role(team.id),
                    "members": members, "active_keys": keys, "spend_30d_usd": round(spend or 0.0, 6)})
    return out


@console_router.get("/teams/{team_id}/members")
def team_members(team_id: str, current: ConsoleUser = Depends(ready_user), db: Session = Depends(get_db)) -> List[dict]:
    team = _team(db, team_id, current)
    rows = (db.query(TeamMember, User).join(User, User.id == TeamMember.user_id)
            .filter(TeamMember.team_id == team.id).order_by(User.name).all())
    keys = db.query(ApiKey).filter(ApiKey.project == team.name, ApiKey.revoked_at.is_(None)).all()
    used = _last_used(db, [k.id for k in keys] + [f"console:{user.id}" for _, user in rows])
    out = []
    for member, user in rows:
        own = [used.get(k.id) for k in keys if k.user_id == user.id] + [used.get(f"console:{user.id}")]
        out.append({"type": "user", "user_id": user.id, "name": user.name, "email": user.email,
                    "role": member.role, "disabled": user.disabled_at is not None,
                    "last_active": _iso(_latest(user.last_login_at, *own))})
    for key in keys:
        if key.user_id is None:
            out.append({"type": "service", "key_id": key.id, "name": key.name, "email": None,
                        "role": "service", "disabled": False, "last_active": _iso(used.get(key.id))})
    return out


@console_router.post("/teams/{team_id}/members", status_code=201)
def add_member(team_id: str, body: MemberIn, current: ConsoleUser = Depends(ready_user),
               db: Session = Depends(get_db)) -> dict:
    team = _team(db, team_id, current, need="admin")
    email = body.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    out: dict = {}
    if user is None:
        if not current.is_admin and _beta_full(db):
            raise HTTPException(status_code=403, detail="the beta is full - no new accounts can be added")
        out["temporary_password"] = temporary_password()
        user = User(email=email, name=(body.name or email.split("@")[0]).strip(), role="member",
                    password_hash=hash_password(out["temporary_password"]), must_change_password=True)
        db.add(user)
        db.flush()
    if db.query(TeamMember).filter(TeamMember.team_id == team.id, TeamMember.user_id == user.id).first():
        raise HTTPException(status_code=409, detail=f"{email} is already in {team.name}")
    db.add(TeamMember(team_id=team.id, user_id=user.id, role=body.role))
    db.commit()
    return {"user_id": user.id, "email": user.email, "name": user.name, "role": body.role, **out}


@console_router.patch("/teams/{team_id}/members/{user_id}")
def change_member_role(team_id: str, user_id: str, body: MemberPatch, current: ConsoleUser = Depends(ready_user),
                       db: Session = Depends(get_db)) -> dict:
    team = _team(db, team_id, current, need="admin")
    member = db.query(TeamMember).filter(TeamMember.team_id == team.id, TeamMember.user_id == user_id).first()
    if member is None:
        raise HTTPException(status_code=404, detail="not a member of this team")
    member.role = body.role
    db.commit()
    return {"user_id": user_id, "role": body.role}


@console_router.delete("/teams/{team_id}/members/{user_id}")
def remove_member(team_id: str, user_id: str, current: ConsoleUser = Depends(ready_user),
                  db: Session = Depends(get_db)) -> dict:
    """Also revokes the member's keys in this team."""
    team = _team(db, team_id, current, need="admin")
    member = db.query(TeamMember).filter(TeamMember.team_id == team.id, TeamMember.user_id == user_id).first()
    if member is None:
        raise HTTPException(status_code=404, detail="not a member of this team")
    keys = db.query(ApiKey).filter(ApiKey.project == team.name, ApiKey.user_id == user_id,
                                   ApiKey.revoked_at.is_(None)).all()
    for key in keys:
        key.revoked_at = _now()
        forget_key(key.key_hash)
    db.delete(member)
    db.commit()
    return {"removed": user_id, "revoked_keys": len(keys)}


@console_router.post("/teams/{team_id}/members/{user_id}/reset-password")
def reset_member_password(team_id: str, user_id: str, current: ConsoleUser = Depends(ready_user),
                          db: Session = Depends(get_db)) -> dict:
    team = _team(db, team_id, current, need="admin")
    if not db.query(TeamMember).filter(TeamMember.team_id == team.id, TeamMember.user_id == user_id).first():
        raise HTTPException(status_code=404, detail="not a member of this team")
    user = db.get(User, user_id)
    if user.role == "admin" and not current.is_admin:
        raise HTTPException(status_code=403, detail="only admins can reset an admin's password")
    password = temporary_password()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    db.commit()
    return {"user_id": user_id, "temporary_password": password}


@console_router.get("/teams/{team_id}/keys")
def team_keys(team_id: str, current: ConsoleUser = Depends(ready_user), db: Session = Depends(get_db)) -> List[dict]:
    team = _team(db, team_id, current)
    query = db.query(ApiKey).filter(ApiKey.project == team.name, ApiKey.revoked_at.is_(None))
    if current.team_role(team.id) != "admin":
        query = query.filter(ApiKey.user_id == current.user.id)
    keys = query.order_by(ApiKey.created_at).all()
    users = {u.id: u for u in db.query(User).filter(User.id.in_([k.user_id for k in keys if k.user_id]))}
    used = _last_used(db, [k.id for k in keys])
    return [{**_key_view(k, users), "last_used": _iso(used.get(k.id))} for k in keys]


@console_router.post("/teams/{team_id}/keys", status_code=201)
def create_team_key(team_id: str, body: TeamKeyIn, current: ConsoleUser = Depends(ready_user),
                    db: Session = Depends(get_db), services: GatewayServices = Depends(get_services)) -> dict:
    team = _team(db, team_id, current)
    is_team_admin = current.team_role(team.id) == "admin"
    owner = current.user.id if body.for_user == "me" else body.for_user
    if owner != current.user.id and not is_team_admin:
        raise HTTPException(status_code=403, detail="members can only create their own keys")
    if owner and owner != current.user.id and not db.query(TeamMember).filter(
            TeamMember.team_id == team.id, TeamMember.user_id == owner).first():
        raise HTTPException(status_code=400, detail="that user is not a member of this team")
    rpm, tpm = body.rpm_limit, body.tpm_limit
    if not current.is_admin:
        # only org admins may give a key more than the gateway's default limits
        defaults = services.config.rate_limits
        rpm = _at_most(rpm, defaults.default_rpm)
        tpm = _at_most(tpm, defaults.default_tpm)
    raw, key_hash = generate_key()
    key = repository.create_api_key(
        db, name=body.name, project=team.name, key_hash=key_hash, key_prefix=raw[:14],
        allowed_models=json.dumps(body.allowed_models) if body.allowed_models is not None else None,
        rpm_limit=rpm, tpm_limit=tpm, daily_budget_usd=body.daily_budget_usd,
        user_id=owner, created_by=current.user.id,
    )
    return {**_key_view(key), "key": raw}


def _at_most(value: Optional[int], ceiling: Optional[int]) -> Optional[int]:
    """`value` capped at `ceiling`; unset stays unset (the default then applies)."""
    return min(value, ceiling) if value is not None and ceiling is not None else value


@console_router.delete("/teams/{team_id}/keys/{key_id}")
def revoke_team_key(team_id: str, key_id: str, current: ConsoleUser = Depends(ready_user),
                    db: Session = Depends(get_db)) -> dict:
    team = _team(db, team_id, current)
    key = db.get(ApiKey, key_id)
    if key is None or key.project != team.name:
        raise HTTPException(status_code=404, detail="key not found in this team")
    if key.user_id != current.user.id and current.team_role(team.id) != "admin":
        raise HTTPException(status_code=403, detail="members can only revoke their own keys")
    revoked = repository.revoke_api_key(db, key_id)
    forget_key(revoked.key_hash)
    return {"revoked": key_id}


@console_router.get("/teams/{team_id}/budgets")
def team_budgets(team_id: str, current: ConsoleUser = Depends(ready_user), db: Session = Depends(get_db),
                 services: GatewayServices = Depends(get_services)) -> List[dict]:
    """The team's budgets and the caller's own, with spend this period."""
    team = _team(db, team_id, current)
    rows = db.query(Budget).filter(or_(and_(Budget.scope == "team", Budget.target == team.name),
                                       and_(Budget.scope == "user", Budget.target == current.user.id))).all()
    return [budgets.status(db, b, services.budgets, services.usage_writer) for b in rows]


def _scope_keys(db: Session, team: Team, current: ConsoleUser) -> Optional[List[str]]:
    """None = the whole team (team admins); otherwise the member's own keys
    plus their playground use."""
    if current.team_role(team.id) == "admin":
        return None
    keys = [k.id for k in db.query(ApiKey).filter(ApiKey.project == team.name, ApiKey.user_id == current.user.id)]
    return keys + [f"console:{current.user.id}"]


@console_router.get("/teams/{team_id}/usage/daily")
def team_usage_daily(team_id: str, days: int = Query(default=30, ge=1, le=90),
                     current: ConsoleUser = Depends(ready_user), db: Session = Depends(get_db)) -> dict:
    team = _team(db, team_id, current)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return {"team": team.name, "days": repository.daily_usage(db, since, team.name, _scope_keys(db, team, current))}


@console_router.get("/teams/{team_id}/usage")
def team_usage(team_id: str, hours: float = Query(default=24 * 30, gt=0, le=24 * 90),
               group_by: Literal["model", "provider", "api_key_id"] = "model",
               current: ConsoleUser = Depends(ready_user), db: Session = Depends(get_db)) -> dict:
    """Team admins see the whole team; members see their own keys."""
    team = _team(db, team_id, current)
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    key_ids = None
    if current.team_role(team.id) != "admin":
        key_ids = [k.id for k in db.query(ApiKey).filter(ApiKey.project == team.name,
                                                         ApiKey.user_id == current.user.id)]
        key_ids.append(f"console:{current.user.id}")  # playground use
    groups = repository.usage_summary(db, since, group_by, project=team.name, api_key_ids=key_ids)
    costs = [g["cost_usd"] for g in groups if g["cost_usd"] is not None]
    return {
        "team": team.name, "scope": "team" if key_ids is None else "mine", "since": since.isoformat(),
        "total": {"requests": sum(g["requests"] for g in groups),
                  "cache_hits": sum(g["cache_hits"] for g in groups),
                  "total_tokens": sum(g["total_tokens"] for g in groups),
                  "cost_usd": round(sum(costs), 8) if costs else None,
                  "unpriced_requests": sum(g["unpriced_requests"] for g in groups)},
        "groups": groups,
    }
