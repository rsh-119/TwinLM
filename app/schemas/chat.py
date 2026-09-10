"""Pydantic request/response schemas for the chat completions API."""
from typing import List, Literal

from pydantic import BaseModel, Field


class ChatMessageIn(BaseModel):
    """A single message in an incoming chat request."""

    role: Literal["user", "assistant", "system"]
    content: str


class ChatCompletionRequest(BaseModel):
    """`messages` normally carries just the new turn - the gateway supplies
    the rest of the conversation from memory. If more than one message is
    given, only the last "user" one is used as the current turn.
    """

    conversation_id: str
    model: str = "auto"
    stream: bool = False
    messages: List[ChatMessageIn] = Field(min_length=1)


class ChatResponseMessage(BaseModel):
    """The assistant's reply, as returned to the client."""

    role: Literal["assistant"] = "assistant"
    content: str


class ChatCompletionResponse(BaseModel):
    """Same shape regardless of which provider answered - `provider` says
    who, `model` says which concrete model they used."""

    id: str
    conversation_id: str
    model: str
    provider: str
    message: ChatResponseMessage
