"""Conversation summarization: a simple, provider-backed memory-compaction step.

`ConversationManager` only ever loads the last `MAX_RECENT_MESSAGES`
messages into context (see `conversation_manager.py`), so once a
conversation runs long, everything older than that would otherwise be lost.
This module folds the full transcript into a short summary that the
context builder includes alongside the recent window, so nothing important
gets forgotten just because it scrolled out of view.

This is deliberately not a "memory agent" - it's one pure function that
asks whichever provider is currently available (via the normal "auto"
fallback chain) to produce a fresh summary. If every provider is down, the
caller simply skips updating the summary for now; it doesn't take down the
chat request itself.
"""
from typing import Dict, List

from app.router.model_router import ModelRouter

# Re-generate the summary once a conversation reaches this many stored
# messages, and every SUMMARY_THRESHOLD messages after that. Messages are
# always stored in user/assistant pairs (see app/api/chat.py), so the count
# only ever increases by 2 and lands exactly on a multiple of this
# threshold - no extra bookkeeping needed to catch it.
SUMMARY_THRESHOLD = 20

_SUMMARY_SYSTEM_PROMPT = (
    "Summarize the following conversation between a user and an assistant "
    "in one concise paragraph. Preserve important facts, decisions, "
    "requirements, unresolved questions, and project context. Write plain "
    "prose, not a transcript, and do not exceed one short paragraph."
)


def should_summarize(message_count: int) -> bool:
    """Return whether a conversation with `message_count` stored messages
    should have its summary (re)generated now."""
    return message_count > 0 and message_count % SUMMARY_THRESHOLD == 0


async def summarize_conversation(router: ModelRouter, messages: List[Dict[str, str]]) -> str:
    """Produce a concise summary of a full conversation transcript.

    Uses whichever provider is currently available (the same "auto"
    fallback chain as regular chat requests), so summarization degrades
    the same way normal requests do rather than needing its own
    provider-specific error handling.
    """
    transcript = "\n\n".join(f"{m['role'].capitalize()}: {m['content']}" for m in messages)
    request_messages = [
        {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": transcript},
    ]
    result = await router.route("auto", request_messages)
    return result.content.strip()
