"""Context builder: assembles the prompt context sent to an LLM provider.

Combines, in order, the conversation summary, the user's long-term
memories, and the recent conversation history into one system-style
context block, followed by the user's current message. This is a pure
function over already-fetched data - it does not touch the database or
know about providers, so it stays trivial to unit test.
"""
from typing import Dict, List, Sequence

_ROLE_LABELS = {"user": "User", "assistant": "Assistant", "system": "System"}


def build_context(
    summary: str,
    memories: Sequence[str],
    recent_messages: Sequence[Dict[str, str]],
    current_message: str,
) -> List[Dict[str, str]]:
    """Build the message list to hand to a provider for this request.

    Args:
        summary: The conversation's stored summary (may be empty/"").
        memories: The user's long-term memory facts, oldest first (may be empty).
        recent_messages: Recent conversation messages as {"role", "content"}
            dicts, oldest first - already limited by the caller (see
            `ConversationManager.get_recent_messages`).
        current_message: The new user message for this request.

    Returns:
        `[{"role": "system", "content": <context block>}, {"role": "user",
        "content": current_message}]`. The system message is omitted
        entirely when there is no summary, memory, or history yet (e.g. the
        very first message of a brand new conversation).
    """
    context = _render_context_block(summary, memories, recent_messages)

    messages: List[Dict[str, str]] = []
    if context:
        messages.append({"role": "system", "content": context})
    messages.append({"role": "user", "content": current_message})
    return messages


def _render_context_block(
    summary: str,
    memories: Sequence[str],
    recent_messages: Sequence[Dict[str, str]],
) -> str:
    """Render the summary, memories, and recent history into one text block."""
    sections: List[str] = []

    if summary:
        sections.append(f"Conversation summary:\n{summary}")

    if memories:
        sections.append("User memory:\n" + "\n".join(memories))

    if recent_messages:
        turns = "\n\n".join(
            f"{_ROLE_LABELS.get(m['role'], m['role'].capitalize())}:\n{m['content']}"
            for m in recent_messages
        )
        sections.append(f"Recent conversation:\n\n{turns}")

    return "\n\n".join(sections)
