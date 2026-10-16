"""Popular providers the admin panel can add with just an API key. Every one
except Gemini speaks the OpenAI chat-completions API, so it uses the
`openai_compatible` adapter. Model lists are suggestions (any model name
the provider accepts works, and "Test connection" fetches the provider's
live list); prices are left to the admin because they change. Anthropic is
reached through its own OpenAI-compatible endpoint.

Each list is ordered lightest/cheapest first, and the first one is the
default: start cheap, move up only where quality needs it. `free_tier`
marks providers with a free plan (rate-limited; terms change, so check)."""
import re
from typing import Dict, Iterable, List, Optional, TypedDict


class Preset(TypedDict):
    label: str
    type: str
    base_url: Optional[str]
    default_model: str
    models: List[str]
    capabilities: List[str]
    needs_key: bool
    key_url: Optional[str]
    auth_header: str  # "authorization" (Bearer) or "api-key"
    note: Optional[str]
    free_tier: bool


def _p(label, type_, base_url, models, capabilities, key_url, needs_key=True,
       auth_header="authorization", note=None, free_tier=False) -> Preset:
    return Preset(label=label, type=type_, base_url=base_url, default_model=models[0] if models else "",
                  models=models, capabilities=capabilities, needs_key=needs_key, key_url=key_url,
                  auth_header=auth_header, note=note, free_tier=free_tier)


PRESETS: Dict[str, Preset] = {
    "anthropic": _p("Anthropic (Claude)", "openai_compatible", "https://api.anthropic.com/v1",
                    ["claude-haiku-5-5", "claude-haiku-4-5", "claude-sonnet-5-5", "claude-opus-5-5"],
                    ["chat", "tools", "vision"], "https://console.anthropic.com/settings/keys"),
    "openai": _p("OpenAI", "openai_compatible", "https://api.openai.com/v1",
                 ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1", "text-embedding-3-small"],
                 ["chat", "tools", "vision"], "https://platform.openai.com/api-keys"),
    "mistral": _p("Mistral", "openai_compatible", "https://api.mistral.ai/v1",
                  ["mistral-small-latest", "mistral-medium-latest", "mistral-large-latest", "codestral-latest"],
                  ["chat", "tools"], "https://console.mistral.ai/api-keys", free_tier=True),
    "deepseek": _p("DeepSeek", "openai_compatible", "https://api.deepseek.com/v1",
                   ["deepseek-chat", "deepseek-reasoner"], ["chat", "tools"],
                   "https://platform.deepseek.com/api_keys"),
    "groq": _p("Groq", "openai_compatible", "https://api.groq.com/openai/v1",
               ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"],
               ["chat", "tools"], "https://console.groq.com/keys", free_tier=True),
    "gemini": _p("Google Gemini", "gemini", None,
                 # "-latest" names follow Google's newest model; fixed ones retire
                 ["gemini-3.1-flash-lite", "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-pro-latest"],
                 ["chat", "tools", "vision"], "https://aistudio.google.com/apikey", free_tier=True),
    "xai": _p("xAI (Grok)", "openai_compatible", "https://api.x.ai/v1",
              ["grok-3-mini", "grok-4"], ["chat", "tools", "vision"], "https://console.x.ai"),
    "together": _p("Together AI", "openai_compatible", "https://api.together.xyz/v1",
                   ["meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen2.5-72B-Instruct-Turbo",
                    "deepseek-ai/DeepSeek-V3"], ["chat", "tools"], "https://api.together.ai/settings/api-keys"),
    "openrouter": _p("OpenRouter", "openai_compatible", "https://openrouter.ai/api/v1",
                     ["openai/gpt-4o-mini", "anthropic/claude-haiku-4.5", "meta-llama/llama-3.3-70b-instruct"],
                     ["chat", "tools", "vision"], "https://openrouter.ai/keys", free_tier=True,
                     note="Models ending in :free cost nothing (rate-limited)."),
    "fireworks": _p("Fireworks AI", "openai_compatible", "https://api.fireworks.ai/inference/v1",
                    ["accounts/fireworks/models/llama-v3p3-70b-instruct"], ["chat", "tools"],
                    "https://fireworks.ai/account/api-keys"),
    "perplexity": _p("Perplexity", "openai_compatible", "https://api.perplexity.ai",
                     ["sonar", "sonar-pro"], ["chat"], "https://www.perplexity.ai/settings/api"),
    "azure": _p("Azure OpenAI", "openai_compatible", "https://YOUR-RESOURCE.openai.azure.com/openai/v1",
                ["gpt-4o-mini", "gpt-4o"], ["chat", "tools", "vision"],
                "https://portal.azure.com", auth_header="api-key",
                note="Replace YOUR-RESOURCE with your Azure OpenAI resource name; the model is your deployment name."),
    "cerebras": _p("Cerebras", "openai_compatible", "https://api.cerebras.ai/v1",
                   ["llama-3.3-70b", "gpt-oss-120b"], ["chat", "tools"], "https://cloud.cerebras.ai", free_tier=True),
    "nvidia": _p("NVIDIA NIM", "openai_compatible", "https://integrate.api.nvidia.com/v1",
                 ["meta/llama-3.3-70b-instruct", "nvidia/llama-3.1-nemotron-70b-instruct"], ["chat", "tools"],
                 "https://build.nvidia.com", free_tier=True),
    "huggingface": _p("Hugging Face", "openai_compatible", "https://router.huggingface.co/v1",
                      ["meta-llama/Llama-3.3-70B-Instruct", "openai/gpt-oss-120b"], ["chat", "tools"],
                      "https://huggingface.co/settings/tokens", free_tier=True),
    "cohere": _p("Cohere", "openai_compatible", "https://api.cohere.ai/compatibility/v1",
                 ["command-r7b-12-2024", "command-a-03-2025"], ["chat", "tools"],
                 "https://dashboard.cohere.com/api-keys", free_tier=True),
    "ollama": _p("Ollama (local)", "openai_compatible", "http://host.docker.internal:11434/v1",
                 ["llama3.2", "qwen2.5"], ["chat"], None, needs_key=False, free_tier=True),
    "lmstudio": _p("LM Studio (local)", "openai_compatible", "http://host.docker.internal:1234/v1",
                   [], ["chat"], None, needs_key=False, free_tier=True,
                   note="Type the model name loaded in LM Studio."),
    "custom": _p("Custom OpenAI-compatible", "openai_compatible", "", [], ["chat"], None, needs_key=False),
}


# Models these patterns match are not for chat (images, speech, embeddings...).
_NOT_CHAT = re.compile(r"image|tts|audio|speech|embed|transcri|realtime|moderation|guard|search|research|dall-e|whisper",
                       re.I)
_LIGHT = re.compile(r"lite|mini|nano|flash|haiku|small|instant|(?<![\d.])[1-9]b\b|r7b", re.I)


def chat_models(models: Iterable[str]) -> List[str]:
    """Drop models that can't answer chat (image, speech, embeddings...)."""
    return [m for m in models if not _NOT_CHAT.search(m)]


def recommended_model(suggested: Iterable[str], available: Iterable[str]) -> Optional[str]:
    """The model to pre-select once the provider's live list is known: the
    first suggestion the key can use (suggestions are lightest first), else
    the first light-sounding chat model, else the first chat model.
    ponytail: name heuristics; providers don't publish size or price in
    their model lists."""
    chat = chat_models(available)
    have = set(chat)
    for model in suggested:
        if model in have:
            return model
    return next((m for m in chat if _LIGHT.search(m)), chat[0] if chat else None)
