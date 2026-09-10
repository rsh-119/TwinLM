"""Combines the conversation summary, user memories, and recent history into
the prompt sent to a provider. Pure function - no database, no provider
knowledge - so it's trivial to test on its own.
"""
from typing import Dict, List, Sequence

_ROLE_LABELS = {"user": "User", "assistant": "Assistant", "system": "System"}


def build_context(
    summary: str,
    memories: Sequence[str],
    recent_messages: Sequence[Dict[str, str]],
    current_message: str,
) -> List[Dict[str, str]]:
    """Returns a "system" message (summary + memories + recent history,
    whichever of those exist) followed by the current message as "user".
    If none of them exist yet, the system message is skipped entirely.
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
