"""Combines the conversation summary, user memories, and recent history into
the prompt sent to a provider. Pure function - no database, no provider
knowledge - so it's trivial to test on its own.
"""
from typing import Dict, List, Sequence

_ROLE_LABELS = {"user": "User", "assistant": "Assistant", "system": "System"}

# Caps any single piece of text (a memory, one recent message, the running
# summary, or the current message) before it goes into the prompt. Nothing
# here is token- or provider-aware - it's a blunt, cheap safety net against
# one oversized paste blowing the request past a provider's rate/size limit
# (this is what actually happened: a ~30KB message pushed a single request
# over Groq's tokens-per-minute quota). ~4000 chars is roughly 1000 tokens -
# generous for a normal turn, small next to any real context window.
MAX_TEXT_CHARS = 4000
MAX_MEMORY_CHARS = 12000

_TRUNCATION_MARKER = "\n\n...[truncated - this was much longer]"


def _truncate(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(_TRUNCATION_MARKER)].rstrip() + _TRUNCATION_MARKER


def build_context(
    summary: str,
    memories: Sequence[str],
    recent_messages: Sequence[Dict[str, str]],
    current_message: str,
) -> List[Dict[str, str]]:
    """Returns a "system" message (summary + memories + recent history,
    whichever of those exist) followed by the current message as "user".
    If none of them exist yet, the system message is skipped entirely.

    Each piece is independently truncated (see `_truncate`) before being
    joined, so one huge message can't balloon the whole request - it just
    gets cut short, rather than the turn failing outright.
    """
    truncated_recent = [{**m, "content": _truncate(m["content"])} for m in recent_messages]
    selected_memories = []
    remaining = MAX_MEMORY_CHARS
    for memory in reversed(memories):
        if remaining <= len(_TRUNCATION_MARKER):
            break
        clipped = _truncate(memory, min(MAX_TEXT_CHARS, remaining))
        if clipped:
            selected_memories.append(clipped)
            remaining -= len(clipped)
        if remaining <= 0:
            break
    context = _render_context_block(_truncate(summary), list(reversed(selected_memories)), truncated_recent)

    messages: List[Dict[str, str]] = []
    if context:
        messages.append({"role": "system", "content": context})
    messages.append({"role": "user", "content": _truncate(current_message)})
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
