"""Application configuration loaded from environment variables.

All settings are read once at import time via ``get_settings()``. Keeping
configuration in a single place means no other module ever reads
``os.environ`` directly, and API keys never end up hardcoded in source.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings.

    Values are loaded from a ``.env`` file (if present) and from the real
    environment, which always takes precedence.
    """

    gemini_api_key: str = ""
    openai_api_key: str = ""

    ollama_base_url: str = "http://localhost:11434"

    default_model: str = "auto"

    database_url: str = "sqlite:///./llm_gateway.db"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()
