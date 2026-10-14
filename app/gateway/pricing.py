"""Request cost from config-driven prices (USD per million tokens, keyed
"provider/model" in gateway.toml). Unknown model -> None, never 0: an
unpriced request must not read as free."""
from typing import Dict, Optional

from app.gateway.config import PriceConfig
from app.providers.base import TokenUsage


def price_key(provider: str, model: str) -> str:
    return f"{provider}/{model}"


def request_cost(
    pricing: Dict[str, PriceConfig], provider: str, model: str, usage: Optional[TokenUsage]
) -> Optional[float]:
    price = pricing.get(price_key(provider, model))
    if price is None or usage is None:
        return None
    return (
        usage.prompt_tokens * price.input_per_million
        + usage.completion_tokens * price.output_per_million
    ) / 1_000_000
