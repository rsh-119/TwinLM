"""Runtime configuration: gateway.toml plus the providers, aliases and prices
added from the admin panel (database), merged into one GatewayConfig and
applied without a restart.

Precedence: gateway.toml is the base. Panel providers are added next to it
(names may not clash). A panel alias replaces a toml alias of the same
name. Panel prices add to or override toml prices. The merged config goes
through the same validation as gateway.toml."""
import asyncio
import json
import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import AliasOverride, PriceOverride, ProviderConfigRow
from app.gateway import secret_box
from app.gateway.config import GatewayConfig, get_gateway_config
from app.gateway.shared import get_redis
from app.router.model_router import ModelRouter, build_router, swap_router

logger = logging.getLogger("twinlm.runtime")

# Old routers keep serving their in-flight requests for this long before
# their HTTP clients are closed.
RETIRE_AFTER_SECONDS = 120.0

_base: Optional[GatewayConfig] = None
_retired: List[ModelRouter] = []


def base_config() -> GatewayConfig:
    """The gateway.toml config (tests may replace it with set_base_config)."""
    return _base or get_gateway_config()


def set_base_config(config: Optional[GatewayConfig]) -> None:
    global _base
    _base = config


def effective_config(db: Session) -> Tuple[GatewayConfig, Dict[str, str]]:
    """Merged config and the decrypted keys of panel providers. Reads the
    session's pending (unflushed or uncommitted) changes too, so a write can
    be validated before it is committed."""
    base = base_config()
    data = base.model_dump()
    keys: Dict[str, str] = {}
    for row in db.query(ProviderConfigRow).all():
        if row.name in base.providers:
            logger.warning("panel provider %r clashes with gateway.toml - ignored", row.name)
            continue
        data["providers"][row.name] = {
            "type": row.type,
            "default_model": row.default_model,
            "base_url": row.base_url or None,
            "timeout_seconds": row.timeout_seconds,
            "enabled": row.enabled,
            "capabilities": json.loads(row.capabilities) if row.capabilities else None,
            "auth_header": row.auth_header or "authorization",
        }
        if row.api_key_encrypted:
            try:
                keys[row.name] = secret_box.decrypt(row.api_key_encrypted)
            except secret_box.SecretUnavailable as exc:
                # the provider stays configured and fails with "api key is
                # not configured" until the key is entered again
                logger.error("provider %r: %s", row.name, exc)
    for row in db.query(AliasOverride).all():
        data["models"][row.alias] = {
            "targets": json.loads(row.targets),
            "max_output_tokens": row.max_output_tokens,
            "cache_ttl_seconds": row.cache_ttl_seconds,
        }
    for row in db.query(PriceOverride).all():
        data["pricing"][row.key] = {"input_per_million": row.input_per_million,
                                    "output_per_million": row.output_per_million}
    return GatewayConfig.model_validate(data), keys


def build(db: Session) -> Tuple[GatewayConfig, ModelRouter]:
    """Merged config and a router for it; raises ValueError (or pydantic's
    ValidationError, a ValueError) when the combination is invalid."""
    config, keys = effective_config(db)
    return config, build_router(config, get_settings(), keys)


def apply(config: GatewayConfig, router: ModelRouter, services) -> None:
    """Make `config`/`router` live: new requests use them immediately."""
    old = swap_router(router)
    services.config = config
    if old is not None and old is not router:
        _retire(old)


def reload(db: Session, services) -> None:
    config, router = build(db)
    apply(config, router, services)


# Several instances (REDIS_URL): a change made on one instance bumps this
# counter; the others notice within FOLLOW_INTERVAL_SECONDS and reload
# from the database.
CONFIG_VERSION_KEY = "twinlm:config_version"
FOLLOW_INTERVAL_SECONDS = 5.0
_seen_version: Optional[bytes] = None


def announce() -> None:
    """Tell the other instances to reload the admin-panel config."""
    global _seen_version
    redis = get_redis()
    if redis is None:
        return
    try:
        _seen_version = str(redis.incr(CONFIG_VERSION_KEY)).encode()
    except Exception:  # noqa: BLE001
        logger.warning("redis unavailable: other instances apply this change after their restart", exc_info=True)


async def follow(services, session_factory, interval: float = FOLLOW_INTERVAL_SECONDS) -> None:
    """Background task: reload when another instance changed the config."""
    global _seen_version
    redis = get_redis()
    _seen_version = await asyncio.to_thread(redis.get, CONFIG_VERSION_KEY)
    while True:
        await asyncio.sleep(interval)
        try:
            version = await asyncio.to_thread(redis.get, CONFIG_VERSION_KEY)
            if version is None or version == _seen_version:
                continue
            _seen_version = version
            db = session_factory()
            try:
                reload(db, services)
            finally:
                db.close()
            logger.info("admin-panel configuration reloaded (changed on another instance)")
        except Exception:  # noqa: BLE001 - keep following
            logger.warning("configuration follow failed", exc_info=True)


def _retire(router: ModelRouter) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _retired.append(router)  # no loop (sync caller): closed at shutdown
        return
    loop.call_later(RETIRE_AFTER_SECONDS, lambda: loop.create_task(router.aclose()))


async def close_retired() -> None:
    while _retired:
        await _retired.pop().aclose()
