"""App settings, loaded from environment variables / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Env vars override .env, which overrides these defaults."""

    gemini_api_key: str = ""
    groq_api_key: str = ""
    openai_api_key: str = ""

    ollama_base_url: str = "http://localhost:11434"

    default_model: str = "auto"

    database_url: str = "sqlite:///./twinlm.db"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
