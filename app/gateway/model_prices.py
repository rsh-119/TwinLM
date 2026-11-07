"""Built-in prices for preset models: (input, output) USD per million tokens,
standard tier. They apply to providers on the preset's own endpoint, under
gateway.toml and console prices. `builtin_prices = false` in gateway.toml turns
them off (a priced model passes the budget check, an unpriced one doesn't).

Only prices confirmed on the provider's own price page or model API. No
moving names like gemini-flash-latest: their model, and price, can change.

ponytail: a snapshot, updated by hand, so it goes stale. Rates that depend on
the request aren't modelled: cached input is billed at full price, Claude
Haiku 5.5 prompts over 100,000 tokens cost 5x (0.50/2.50), and DeepSeek is
listed at its peak rate (off-peak is half). A fetched price list is the
upgrade if this drifts."""
from typing import Dict, Tuple

PRICES_AS_OF = "2026-11-06"

BUILTIN_PRICES: Dict[str, Dict[str, Tuple[float, float]]] = {
    "anthropic": {
        "claude-haiku-5-5": (0.10, 0.50),
        "claude-haiku-4-5": (1.00, 5.00),
        "claude-sonnet-5-5": (2.00, 10.00),
        "claude-opus-5-5": (4.00, 20.00),
    },
    "openai": {
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4o": (2.50, 10.00),
        "gpt-4.1-mini": (0.40, 1.60),
        "gpt-4.1": (2.00, 8.00),
        "text-embedding-3-small": (0.02, 0.00),
    },
    "gemini": {"gemini-3.1-flash-lite": (0.25, 1.50)},
    "groq": {
        "openai/gpt-oss-20b": (0.075, 0.30),
        "openai/gpt-oss-120b": (0.15, 0.60),
        "qwen/qwen3.8-27b": (0.80, 4.00),
    },
    "deepseek": {"deepseek-flash": (0.30, 1.20), "deepseek-v4-pro": (1.32, 3.96)},
    "perplexity": {"sonar": (0.25, 2.50)},
    "zai": {"glm-5.3-flash": (0.15, 0.50), "glm-5.3": (1.40, 4.40)},
    "minimax": {"MiniMax-M3.1-Flash": (0.15, 0.60), "MiniMax-M3": (0.30, 1.20)},
    "deepinfra": {
        "deepseek-ai/DeepSeek-V4.1-Flash": (0.20, 0.60),
        "zai-org/GLM-5.3-Flash": (0.15, 0.50),
        "Qwen/Qwen3.6-27B": (0.32, 3.20),
    },
    "novita": {
        "deepseek/deepseek-v4.1-flash": (0.30, 1.20),
        "qwen/qwen3.8-flash": (0.15, 0.47),
        "minimax/minimax-m3": (0.30, 1.20),
    },
    "sambanova": {
        "MiniMax-M3.1-Flash": (0.30, 1.20),
        "gpt-oss-120b": (0.22, 0.59),
        "Meta-Llama-3.3-70B-Instruct": (0.60, 1.20),
    },
    "openrouter": {
        "openai/gpt-4o-mini": (0.15, 0.60),
        "anthropic/claude-haiku-4.5": (1.00, 5.00),
        "meta-llama/llama-3.3-70b-instruct": (0.59, 0.79),
    },
}
