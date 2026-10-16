"""Gateway behavior config, loaded from a TOML file (`GATEWAY_CONFIG_PATH`,
default `gateway.toml`). Validated with Pydantic so a typo fails at startup
instead of on the first request. Secrets never live here - providers name
the `Settings` field holding their key (`api_key_setting`)."""
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import get_settings

GuardrailAction = Literal["block", "redact", "log"]
# chat = text chat; tools, vision (image parts) and embeddings gate routing
Capability = Literal["chat", "tools", "vision", "embeddings"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetryConfig(_Strict):
    # Retries of the *same* provider, before falling back to the next one.
    max_retries: int = Field(default=1, ge=0, le=5)
    backoff_base_seconds: float = Field(default=0.2, ge=0)
    backoff_max_seconds: float = Field(default=2.0, ge=0)


class CircuitBreakerConfig(_Strict):
    failure_threshold: int = Field(default=5, ge=1)
    cooldown_seconds: float = Field(default=30.0, gt=0)


class ProviderConfig(_Strict):
    type: Literal["gemini", "openai_compatible", "mock"]
    enabled: bool = True
    default_model: str
    base_url: Optional[str] = None
    api_key_setting: Optional[str] = None  # name of a `Settings` field
    timeout_seconds: float = Field(default=30.0, gt=0)
    # openai_compatible: how the key is sent - "authorization" (Bearer) or
    # "api-key" (Azure OpenAI)
    auth_header: Literal["authorization", "api-key"] = "authorization"
    # None = the adapter type's defaults (see DEFAULT_CAPABILITIES)
    capabilities: Optional[List[Capability]] = None
    # mock only
    mock_latency_seconds: float = 0.0
    mock_failure_rate: float = Field(default=0.0, ge=0, le=1)


class DeploymentConfig(_Strict):
    """A named (provider, model) - an identity for logs, metrics and
    routing that is separate from the alias and the provider."""

    provider: str
    model: str
    # None = the provider's capabilities
    capabilities: Optional[List[Capability]] = None


class ModelAliasConfig(_Strict):
    # Each entry is a deployment id, "provider/model", or a bare provider
    # (its default model).
    # `targets`: ordered fallback; each target is its own model group.
    # `groups`: ordered model groups; deployments inside a group serve the
    # same model and are tried before falling back to the next group.
    targets: List[str] = []
    groups: List[List[str]] = []
    description: str = ""
    # Output-token cap for this alias (overrides default_max_output_tokens):
    # requests without max_tokens get it, larger ones are clamped to it.
    max_output_tokens: Optional[int] = Field(default=None, gt=0)
    # Response-cache TTL for this alias; 0 = never cache it.
    cache_ttl_seconds: Optional[float] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _one_shape(self) -> "ModelAliasConfig":
        if bool(self.targets) == bool(self.groups):
            raise ValueError("set exactly one of `targets` or `groups`")
        if any(not group for group in self.groups):
            raise ValueError("model groups must not be empty")
        return self

    def group_specs(self) -> List[List[str]]:
        return self.groups or [[target] for target in self.targets]


class PriceConfig(_Strict):
    input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)


class RateLimitConfig(_Strict):
    # Defaults for API keys that don't set their own; omitted = unlimited.
    default_rpm: Optional[int] = Field(default=None, gt=0)
    default_tpm: Optional[int] = Field(default=None, gt=0)


class SemanticCacheConfig(_Strict):
    # Aliases whose answers may be reused for a *differently worded* prompt
    # with the same meaning (e.g. an FAQ bot). Only the last user message
    # may differ; everything before it must match exactly. Each eligible
    # cache miss costs one embeddings call. Empty = off.
    models: List[str] = []
    embedding_model: str = "embed"  # alias or deployment with `embeddings`
    # cosine similarity needed for a hit; lower = more hits and more wrong
    # answers ("capital of France" vs "capital of Germany" score ~0.9)
    min_similarity: float = Field(default=0.95, gt=0, le=1)
    max_entries: int = Field(default=1000, gt=0)


class CacheConfig(_Strict):
    # Exact-match; only stateless, non-streaming, deterministic requests
    # (temperature unset or 0) without tool calls or sensitive content.
    enabled: bool = False
    ttl_seconds: float = Field(default=300.0, gt=0)
    max_entries: int = Field(default=1000, gt=0)
    max_bytes: int = Field(default=16_777_216, gt=0)
    semantic: SemanticCacheConfig = SemanticCacheConfig()


class GuardrailsConfig(_Strict):
    # guardrail name ("secrets", "pii", "prompt_injection") -> action
    input: Dict[str, GuardrailAction] = {}
    output: Dict[str, GuardrailAction] = {}
    max_input_chars: Optional[int] = Field(default=None, gt=0)


class StructuredOutputConfig(_Strict):
    max_repair_attempts: int = Field(default=1, ge=0, le=3)


class SavingsConfig(_Strict):
    # "provider/model" price key the business would otherwise use for
    # everything; /admin/savings compares actual cost against it.
    baseline: Optional[str] = None


class GatewayConfig(_Strict):
    request_timeout_seconds: float = Field(default=60.0, gt=0)
    # Output-token cap for every request unless its alias sets its own.
    default_max_output_tokens: Optional[int] = Field(default=None, gt=0)
    max_stream_output_chars: int = Field(default=250_000, gt=0)
    routing_strategy: Literal["priority", "latency", "cost", "load"] = "priority"
    retry: RetryConfig = RetryConfig()
    circuit_breaker: CircuitBreakerConfig = CircuitBreakerConfig()
    providers: Dict[str, ProviderConfig]
    deployments: Dict[str, DeploymentConfig] = {}
    models: Dict[str, ModelAliasConfig] = {}
    pricing: Dict[str, PriceConfig] = {}
    rate_limits: RateLimitConfig = RateLimitConfig()
    cache: CacheConfig = CacheConfig()
    guardrails: GuardrailsConfig = GuardrailsConfig()
    structured_output: StructuredOutputConfig = StructuredOutputConfig()
    savings: SavingsConfig = SavingsConfig()

    @model_validator(mode="after")
    def _baseline_is_priced(self) -> "GatewayConfig":
        if self.savings.baseline and self.savings.baseline not in self.pricing:
            raise ValueError(f"savings.baseline {self.savings.baseline!r} needs a [pricing] entry")
        return self

    def output_cap(self, model: str) -> Optional[int]:
        alias = self.models.get(model)
        if alias is not None and alias.max_output_tokens is not None:
            return alias.max_output_tokens
        return self.default_max_output_tokens

    def cache_ttl(self, model: str) -> Optional[float]:
        """None = the cache default; 0 = do not cache this model."""
        alias = self.models.get(model)
        return alias.cache_ttl_seconds if alias is not None else None


def load_gateway_config(path: str) -> GatewayConfig:
    with Path(path).open("rb") as f:
        return GatewayConfig.model_validate(tomllib.load(f))


@lru_cache
def get_gateway_config() -> GatewayConfig:
    return load_gateway_config(get_settings().gateway_config_path)
