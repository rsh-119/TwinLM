"""App settings. Two sources, deliberately split:

- Environment / .env (`Settings`): secrets and deployment switches.
- `gateway.toml` (`app.gateway.config`): providers, model aliases, routing,
  retries, pricing, limits - behavior, never secrets.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Env vars override .env, which overrides these defaults."""

    gemini_api_key: str = ""
    groq_api_key: str = ""
    openai_api_key: str = ""
    nvidia_api_key: str = ""

    ollama_base_url: str = "http://localhost:11434"

    default_model: str = "auto"

    database_url: str = "sqlite:///./twinlm.db"
    # File holding the database password, used when DATABASE_URL has none
    # (Compose: the generated secret), so every process - the server and
    # `python -m app.ops` - connects the same way.
    database_password_file: str = ""
    # false = refuse to start on an outdated schema instead of migrating it
    migrate_on_startup: bool = True
    # Persistent PostgreSQL connections. Too small a pool makes every
    # overflow request open (and drop) a new connection - measured as the
    # database CPU bottleneck under load.
    db_pool_size: int = 20
    # Seconds a validated API key is reused without a database lookup.
    # Revocation through the admin API takes effect immediately on this
    # (single) instance; direct database edits within this window.
    auth_cache_seconds: float = 30.0
    usage_spool_path: str = "./data/usage-spool.db"
    max_concurrent_requests: int = 100

    gateway_config_path: str = "gateway.toml"

    # Require a gateway API key (Authorization: Bearer gw_live_...) on /v1.
    # On by default; set AUTH_ENABLED=false for a single-user local setup.
    auth_enabled: bool = True
    # Bearer token for /admin. Empty disables the admin API entirely - and
    # with auth on, startup refuses to run without it (no way to make keys).
    admin_token: str = ""
    # The browser UI has no login, so it's off unless asked for.
    web_ui_enabled: bool = False
    # Gateway-owned conversations and memories (app/api/stateful.py). Off =
    # stateless proxy only: `conversation_id` is rejected and the
    # conversation/memory APIs are not mounted.
    stateful_enabled: bool = True
    # Shared-user deployments: only user-bound API keys may use
    # conversations/memories (service keys get 403).
    stateful_requires_user_keys: bool = False

    log_format: str = "json"  # "json" or "text"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
