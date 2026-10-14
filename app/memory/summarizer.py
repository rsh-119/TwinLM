"""Summarize recent conversation turns while retaining older context."""
from typing import Dict, List

from app.router.model_router import ModelRouter, RouteResult

# The number of messages after which to summarize the conversation.
SUMMARY_THRESHOLD = 20

# ~6k tokens. Older text beyond this is dropped from the summarization
# input (newest kept) - the previous summary already covered it.
MAX_TRANSCRIPT_CHARS = 24000
MAX_PREVIOUS_SUMMARY_CHARS = 4000

_SUMMARY_SYSTEM_PROMPT = (
    "Summarize the following conversation between a user and an assistant "
    "in one concise paragraph. Preserve important facts, decisions, "
    "requirements, unresolved questions, and project context. Write plain "
    "prose, not a transcript, and do not exceed one short paragraph."
)


def should_summarize(message_count: int) -> bool:
    return message_count > 0 and message_count % SUMMARY_THRESHOLD == 0


def build_transcript(messages: List[Dict[str, str]], limit: int = MAX_TRANSCRIPT_CHARS) -> str:
    transcript = "\n\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in messages)
    if len(transcript) <= limit:
        return transcript
    return "[earlier messages omitted]\n\n" + transcript[-limit:]


async def summarize_conversation(
    router: ModelRouter, messages: List[Dict[str, str]], previous_summary: str = "",
    model: str = "auto",
) -> RouteResult:
    transcript = build_transcript(messages)
    if previous_summary:
        transcript = (
            "Previous summary (retain relevant facts and decisions):\n"
            + previous_summary[-MAX_PREVIOUS_SUMMARY_CHARS:]
            + "\n\nRecent conversation:\n"
            + transcript
        )
    request_messages = [
        {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": transcript},
    ]
    return await router.route(model, request_messages)
