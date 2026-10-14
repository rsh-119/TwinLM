"""Pydantic request/response schemas for the chat completions API.

The wire format is a superset of OpenAI's chat completions API, so the
OpenAI SDK works against it unchanged. TwinLM's own additions
(`conversation_id`, `provider`, `message`) sit alongside the OpenAI fields.
"""
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from app.config import get_settings


class _StrictWire(BaseModel):
    """Unknown fields are rejected (400 `unsupported_parameter`, see
    app/main.py) instead of silently dropped. Unknown fields that are
    explicitly null are tolerated: SDKs echo absent optional fields as null."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _drop_null_extras(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None or k in cls.model_fields}
        return data


class TextPart(BaseModel):
    type: Literal["text"]
    text: str


class ImageURL(BaseModel):
    url: str
    detail: Optional[str] = None


class ImagePart(BaseModel):
    type: Literal["image_url"]
    image_url: ImageURL


ContentPart = Annotated[Union[TextPart, ImagePart], Field(discriminator="type")]


class ChatMessageIn(_StrictWire):
    """A single message in an incoming chat request. `content` is a string,
    a list of content parts, or null on an assistant turn that only calls
    tools. `tool_calls` are kept as sent (provider-specific extras, like
    Gemini's thought signature, must survive the round trip)."""

    role: Literal["user", "assistant", "system", "tool"]
    content: Optional[Union[str, List[ContentPart]]] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None

    @model_validator(mode="after")
    def _check_shape(self) -> "ChatMessageIn":
        if self.content is None and not (self.role == "assistant" and self.tool_calls):
            raise ValueError("content is required unless an assistant message has tool_calls")
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("tool messages require tool_call_id")
        return self


class JsonSchemaFormat(BaseModel):
    name: str = "response"
    description: Optional[str] = None
    schema_: Dict[str, Any] = Field(alias="schema")
    strict: Optional[bool] = None


class ResponseFormat(BaseModel):
    """OpenAI `response_format`. JSON modes are enforced by the gateway
    (instruction + validation + bounded repair), for every provider."""

    type: Literal["text", "json_object", "json_schema"] = "text"
    json_schema: Optional[JsonSchemaFormat] = None


class StreamOptions(BaseModel):
    include_usage: bool = False


class ChatCompletionRequest(_StrictWire):
    """Two modes, picked by `conversation_id`:

    - set: stateful. `messages` normally carries just the new turn - the
      gateway supplies the rest of the conversation from memory, and the
      exchange is stored. If more than one message is given, only the last
      "user" one is used as the current turn.
    - omitted: stateless, OpenAI-style. `messages` is sent to the provider
      as-is and nothing is stored.

    `tools` work in stateless mode only. Fields not declared here (e.g.
    `logprobs`, `logit_bias`, `audio`) are rejected with 400; `n > 1` is
    rejected. `user` is accepted and not forwarded. `max_completion_tokens`
    is OpenAI's newer name for `max_tokens`. See docs/ARCHITECTURE.md
    (compatibility matrix).
    """

    conversation_id: Optional[str] = None
    model: str = Field(default_factory=lambda: get_settings().default_model)
    stream: bool = False
    messages: List[ChatMessageIn] = Field(min_length=1, max_length=200)

    temperature: Optional[float] = Field(default=None, ge=0, le=2)
    top_p: Optional[float] = Field(default=None, ge=0, le=1)
    max_tokens: Optional[int] = Field(default=None, gt=0, le=32768)
    max_completion_tokens: Optional[int] = Field(default=None, gt=0, le=32768)
    stop: Optional[Union[str, List[str]]] = Field(default=None, max_length=4)
    presence_penalty: Optional[float] = Field(default=None, ge=-2, le=2)
    frequency_penalty: Optional[float] = Field(default=None, ge=-2, le=2)
    seed: Optional[int] = None
    user: Optional[str] = Field(default=None, max_length=256)
    response_format: Optional[ResponseFormat] = None
    tools: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=64)
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    parallel_tool_calls: Optional[bool] = None
    stream_options: Optional[StreamOptions] = None
    n: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _one_max_tokens(self) -> "ChatCompletionRequest":
        if self.max_completion_tokens is not None:
            if self.max_tokens is not None and self.max_tokens != self.max_completion_tokens:
                raise ValueError("max_tokens and max_completion_tokens disagree; send one")
            self.max_tokens = self.max_completion_tokens
        return self

    def generation_params(self) -> dict:
        """The provider-facing params the client actually set, by OpenAI name."""
        return self.model_dump(
            include={
                "temperature", "top_p", "max_tokens", "stop", "presence_penalty", "frequency_penalty",
                "seed", "tools", "tool_choice", "parallel_tool_calls",
            },
            exclude_none=True,
        )

    def latest_user_text(self) -> Optional[str]:
        """Text of the last user message, or None when there is none."""
        for message in reversed(self.messages):
            if message.role == "user":
                if isinstance(message.content, list):
                    return "".join(p.text for p in message.content if p.type == "text")
                return message.content
        return None


class ChatResponseMessage(BaseModel):
    """The assistant's reply, as returned to the client. `content` is null
    when the model only called tools."""

    role: Literal["assistant"] = "assistant"
    content: Optional[str]
    tool_calls: Optional[List[Dict[str, Any]]] = None

    @model_serializer(mode="wrap")
    def _omit_absent_tool_calls(self, handler):
        data = handler(self)
        if data.get("tool_calls") is None:
            data.pop("tool_calls", None)
        return data


class ChatChoice(BaseModel):
    """OpenAI `choices[]` entry. TwinLM always returns exactly one."""

    index: int = 0
    message: ChatResponseMessage
    finish_reason: str = "stop"


class TokenUsageOut(BaseModel):
    """Token counts for this turn, as reported by the provider that
    answered it."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    """Same shape regardless of which provider answered - `provider` says
    who, `model` says which concrete model they used. `usage` is omitted
    when the provider's response didn't report token counts.

    `message` duplicates `choices[0].message`; it predates OpenAI
    compatibility and is kept for existing clients."""

    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    conversation_id: Optional[str] = None
    model: str
    provider: str
    choices: List[ChatChoice]
    message: ChatResponseMessage
    usage: Optional[TokenUsageOut] = None
