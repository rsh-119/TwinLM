
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.database import repository
from app.database.models import Conversation, Message
from app.providers.base import TokenUsage

# The maximum number of recent messages to include in the context sent to the LLM provider.
MAX_RECENT_MESSAGES = 10


class ConversationNotFoundError(Exception):
    pass


class ConversationManager:
    """Manages conversations and messages in the database, scoped to one
    project and, when `user_id` is given, to one user: anyone else's
    conversation is reported as not found."""

    def __init__(self, db: Session, project: str = "default", user_id: Optional[str] = None) -> None:
        self._db = db
        self._project = project
        self._user_id = user_id

    @property
    def db(self) -> Session:
        return self._db

    def create_conversation(self, user_id: str) -> Conversation:
        return repository.create_conversation(self._db, user_id=user_id, project=self._project)

    def get_conversation(self, conversation_id: str) -> Conversation:
        conversation = repository.get_conversation(self._db, conversation_id)
        if (
            conversation is None
            or conversation.project != self._project
            or (self._user_id is not None and conversation.user_id != self._user_id)
        ):
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
        usage: Optional[TokenUsage] = None,
    ) -> Message:
        self.get_conversation(conversation_id)
        return repository.add_message(
            self._db,
            conversation_id=conversation_id,
            role=role,
            content=content,
            model=model,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            total_tokens=usage.total_tokens if usage else None,
        )

    def save_exchange(
        self, conversation_id: str, expected_version: int, user_content: str,
        assistant_content: str, model: Optional[str] = None,
        usage: Optional[TokenUsage] = None,
    ) -> None:
        self.get_conversation(conversation_id)
        repository.add_exchange(
            self._db, conversation_id, self._project, expected_version,
            user_content, assistant_content, model,
            usage.prompt_tokens if usage else None,
            usage.completion_tokens if usage else None,
            usage.total_tokens if usage else None,
        )

    def get_recent_messages(
        self, conversation_id: str, limit: int = MAX_RECENT_MESSAGES
    ) -> Sequence[Message]:
        self.get_conversation(conversation_id)
        return repository.get_messages(self._db, conversation_id, limit=limit)

    def get_conversation_summary(self, conversation_id: str) -> str:
        conversation = self.get_conversation(conversation_id)
        return conversation.summary or ""

    def update_summary(
        self, conversation_id: str, summary: str, expected_version: Optional[int] = None
    ) -> Conversation:
        self.get_conversation(conversation_id)
        conversation = repository.update_conversation_summary(
            self._db, conversation_id, summary, expected_version
        )
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

    def get_total_tokens(self, conversation_id: str) -> int:
        """Sum of token usage across every turn in the conversation."""
        self.get_conversation(conversation_id)
        return repository.get_total_tokens(self._db, conversation_id)
