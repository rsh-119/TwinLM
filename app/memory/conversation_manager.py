
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.database import repository
from app.database.models import Conversation, Message

# The maximum number of recent messages to include in the context sent to the LLM provider.
MAX_RECENT_MESSAGES = 10


class ConversationNotFoundError(Exception):
    pass


class ConversationManager:
    """Manages conversations and messages in the database."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def create_conversation(self, user_id: str) -> Conversation:
        return repository.create_conversation(self._db, user_id=user_id)

    def get_conversation(self, conversation_id: str) -> Conversation:
        conversation = repository.get_conversation(self._db, conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)
        return conversation

    def delete_conversation(self, conversation_id: str) -> None:
        """Permanently delete a conversation and all its messages."""
        self.get_conversation(conversation_id)  # raises ConversationNotFoundError if missing
        repository.delete_conversation(self._db, conversation_id)

    def save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        model: Optional[str] = None,
    ) -> Message:
        self.get_conversation(conversation_id)
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
        self.get_conversation(conversation_id)
        return repository.get_messages(self._db, conversation_id, limit=limit)

    def get_conversation_summary(self, conversation_id: str) -> str:
        conversation = self.get_conversation(conversation_id)
        return conversation.summary or ""

    def update_summary(self, conversation_id: str, summary: str) -> Conversation:
        conversation = repository.update_conversation_summary(self._db, conversation_id, summary)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)
        return conversation

    def get_message_count(self, conversation_id: str) -> int:
        self.get_conversation(conversation_id)
        return repository.count_messages(self._db, conversation_id)

    def get_all_messages(self, conversation_id: str) -> Sequence[Message]:
        """Full history, oldest first - used for summarization."""
        self.get_conversation(conversation_id)
        return repository.get_messages(self._db, conversation_id)
