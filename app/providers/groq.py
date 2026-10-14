"""Groq: an OpenAI-compatible provider with Groq's defaults. Kept as its own
class so `GroqProvider()` works standalone (scripts, tests); the gateway
itself builds providers from gateway.toml."""
from typing import Optional

from app.config import get_settings
from app.providers.openai_compatible import OpenAICompatibleProvider, _is_token_rate_limited  # noqa: F401

GROQ_API_BASE = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"


class GroqProvider(OpenAICompatibleProvider):
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = GROQ_API_BASE,
        default_model: str = DEFAULT_GROQ_MODEL,
        timeout_seconds: float = 30.0,
    ) -> None:
        super().__init__(
            name="groq",
            base_url=base_url,
            api_key=api_key if api_key is not None else get_settings().groq_api_key,
            default_model=default_model,
            timeout_seconds=timeout_seconds,
        )
