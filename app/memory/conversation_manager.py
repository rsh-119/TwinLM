"""Conversation manager: the gateway's single source of truth for conversations.

This module owns conversation lifecycle and message storage - creating
conversations, saving messages, and reading them back. It only talks to the
database (via `app.database.repository`) and knows nothing about LLM
providers or routing; that logic lives in `app/providers/` and
`app/router/model_router.py` respectively. This separation is what lets the
gateway swap providers mid-conversation without touching how the
conversation itself is stored.
"""
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.database import repository
from app.database.models import Conversation, Message

# How many of the most recent messages to load into context per request.
# Older history is expected to live in the conversation summary instead (see
# app/memory/context_builder.py and conversation summarization).
MAX_RECENT_MESSAGES = 10


class ConversationNotFoundError(Exception):
    """Raised when an operation references a conversation that doesn't exist."""


class ConversationManager:
    """Creates conversations and reads/writes their messages and summary."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def create_conversation(self, user_id: str) -> Conversation:
        """Start a new, empty conversation for a user."""
        return repository.create_conversation(self._db, user_id=user_id)

    def get_conversation(self, conversation_id: str) -> Conversation:
        """Fetch a conversation by id.

        Raises:
            ConversationNotFoundError: if no such conversation exists.
        """
        conversation = repository.get_conversation(self._db, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)
        return conversation

    def save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        model: Optional[str] = None,
    ) -> Message:
        """Append a message to a conversation.

        Raises:
            ConversationNotFoundError: if the conversation doesn't exist.
        """
        self.get_conversation(conversation_id)  # ensures it exists (or raises)
        return repository.add_message(
            self._db,
            conversation_id=conversation_id,
            role=role,
            content=content,
            model=model,
        )

    def get_recent_messages(
        self, conversation_id: str, limit: int = MAX_RECENT_MESSAGES
    ) -> Sequence[Message]:
        """Return the most recent `limit` messages for a conversation, oldest first.

        Raises:
            ConversationNotFoundError: if the conversation doesn't exist.
        """
        self.get_conversation(conversation_id)
        return repository.get_messages(self._db, conversation_id, limit=limit)

    def get_conversation_summary(self, conversation_id: str) -> str:
        """Return the conversation's stored summary, or "" if none yet.

        Raises:
            ConversationNotFoundError: if the conversation doesn't exist.
        """
        conversation = self.get_conversation(conversation_id)
        return conversation.summary or ""
