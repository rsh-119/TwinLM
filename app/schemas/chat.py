"""Pydantic request/response schemas for the chat completions API."""
from typing import List, Literal

from pydantic import BaseModel, Field


class ChatMessageIn(BaseModel):
    """A single message in an incoming chat request."""

    role: Literal["user", "assistant", "system"]
    content: str


class ChatCompletionRequest(BaseModel):
    """Request body for POST /v1/chat/completions.

    `messages` normally carries just the new turn (e.g. one "user" message)
    - the gateway supplies the rest of the conversation from its own memory,
    so the client never has to resend prior history. If more than one
    message is given, only the last "user" message is used as the current
    turn.
    """

    conversation_id: str
    model: str = "auto"
    messages: List[ChatMessageIn] = Field(min_length=1)


class ChatResponseMessage(BaseModel):
    """The assistant's reply, as returned to the client."""

    role: Literal["assistant"] = "assistant"
    content: str


class ChatCompletionResponse(BaseModel):
    """Normalized response body for POST /v1/chat/completions.

    The shape is identical no matter which provider actually generated the
    reply - `provider` says who answered, `model` says which concrete model
    they used.
    """

    id: str
    conversation_id: str
    model: str
    provider: str
    message: ChatResponseMessage
