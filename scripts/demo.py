"""CLI demo: shows the gateway's core idea end-to-end.

Creates a conversation, sends a couple of messages through normal "auto"
routing, simulates the primary provider going down mid-conversation, and
shows that the fallback provider answers using the same preserved context -
all without the user ever resending earlier messages.

This calls the real FastAPI app in-process (no separate server needed) and
uses your real provider credentials from .env, so it makes real network
calls to whichever providers you have configured.

Run from the repo root:

    python scripts/demo.py
"""
import sys
from pathlib import Path

# Allow running as `python scripts/demo.py` from the repo root without
# installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.providers.gemini import GeminiProvider  # noqa: E402
from app.providers.groq import GroqProvider  # noqa: E402
from app.router.model_router import ModelRouter, get_model_router  # noqa: E402


def send(client: TestClient, conversation_id: str, message: str, model: str = "auto") -> dict:
    """Send one chat turn and return the normalized response body."""
    response = client.post(
        "/v1/chat/completions",
        json={
            "conversation_id": conversation_id,
            "model": model,
            "messages": [{"role": "user", "content": message}],
        },
    )
    response.raise_for_status()
    return response.json()


def main() -> None:
    print("=" * 70)
    print("LLM Gateway demo")
    print("=" * 70)

    with TestClient(app) as client:
        conversation_id = client.post(
            "/v1/conversations", json={"user_id": "demo_user"}
        ).json()["conversation_id"]
        print(f"\n[1] Created conversation: {conversation_id}")

        print("\n[2] User: I am building a Kafka based fraud detection system.")
        result = send(
            client,
            conversation_id,
            "I am building a Kafka based fraud detection system. "
            "Acknowledge in one short sentence.",
        )
        print(f"    Gateway (provider={result['provider']}): {result['message']['content']}")

        print("\n[3] User: How should I handle retries?")
        result = send(client, conversation_id, "How should I handle retries? One short sentence.")
        print(f"    Gateway (provider={result['provider']}): {result['message']['content']}")

        print("\n[4] Simulating a provider outage (e.g. quota exceeded)...")
        print("    Gemini -> unavailable")
        broken_router = ModelRouter(
            {
                "gemini": GeminiProvider(api_key=""),  # forces ProviderUnavailableError
                "groq": GroqProvider(),
            }
        )
        app.dependency_overrides[get_model_router] = lambda: broken_router

        print("\n[5] User: What system did I say I'm building, and how should I optimize the consumer?")
        result = send(
            client,
            conversation_id,
            "In one short sentence: what system did I say I'm building, and how "
            "should I optimize the consumer?",
        )
        print(f"    Fallback -> provider={result['provider']}")
        print(f"    Gateway: {result['message']['content']}")

        preserved = "kafka" in result["message"]["content"].lower()
        print(f"\nContext preserved across the provider switch: {'YES' if preserved else 'NO'}")

        app.dependency_overrides.clear()

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
