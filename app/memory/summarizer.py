"""Summarizes conversations when they get too long. Pure function - no database, --- IGNORE --- no provider knowledge - so it's trivial to test on its own."""
from typing import Dict, List

from app.router.model_router import ModelRouter

# The number of messages after which to summarize the conversation.
SUMMARY_THRESHOLD = 20

_SUMMARY_SYSTEM_PROMPT = (
    "Summarize the following conversation between a user and an assistant "
    "in one concise paragraph. Preserve important facts, decisions, "
    "requirements, unresolved questions, and project context. Write plain "
    "prose, not a transcript, and do not exceed one short paragraph."
)


def should_summarize(message_count: int) -> bool:
    return message_count > 0 and message_count % SUMMARY_THRESHOLD == 0


async def summarize_conversation(router: ModelRouter, messages: List[Dict[str, str]]) -> str:
    transcript = "\n\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in messages)
    request_messages = [
        {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": transcript},
    ]
    result = await router.route("auto", request_messages)
    return result.content.strip()
