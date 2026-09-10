from __future__ import annotations

from typing import Any, Literal, NewType, TypeAlias
from pydantic import BaseModel, ConfigDict, Field
from .policy import ClientPolicy

# These identifiers are intentionally opaque at the provider-contract boundary.
# Their values are owned by the caller/gateway and must not be parsed as provider
# URLs, credentials, or transcript data.
ResponseId = NewType("ResponseId", str)
ConversationId = NewType("ConversationId", str)

OpenAIErrorType: TypeAlias = Literal[
    "invalid_request_error",
    "authentication_error",
    "permission_error",
    "not_found_error",
    "rate_limit_error",
    "server_error",
    "api_error",
]


class OpenAIError(BaseModel):
    """Provider-neutral error detail using the OpenAI-compatible wire shape."""

    message: str
    type: OpenAIErrorType = "invalid_request_error"
    param: str | None = None
    code: str | None = None


class OpenAIErrorResponse(BaseModel):
    """Top-level OpenAI-compatible error envelope."""

    error: OpenAIError


class ValidationIssue(BaseModel):
    """Safe validation metadata that identifies a field without retaining input."""

    loc: tuple[str | int, ...] = ()
    message: str
    type: str


class Message(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Any = ""
    name: str | None = None


class CanonicalMessage(BaseModel):
    """Provider-independent conversation message."""
    role: Literal["system", "user", "assistant", "tool"]
    content: Any = ""
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None


class CanonicalTool(BaseModel):
    type: Literal["function"] = "function"
    function: dict[str, Any]


class CanonicalRequest(BaseModel):
    model: str
    messages: list[CanonicalMessage]
    tools: list[CanonicalTool] = Field(default_factory=list)
    tool_choice: Any = None
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    conversation_id: ConversationId | None = None
    previous_response_id: ResponseId | None = None


def canonicalize(request: ChatRequest) -> CanonicalRequest:
    return CanonicalRequest(
        model=request.model,
        messages=[CanonicalMessage.model_validate(message.model_dump()) for message in request.messages],
        tools=[CanonicalTool.model_validate(tool) for tool in request.tools or []],
        tool_choice=request.tool_choice,
        stream=request.stream,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        conversation_id=request.conversation_id or request.user,
        previous_response_id=request.previous_response_id,
    )


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str = "deepseek-chat"
    messages: list[Message]
    stream: bool = False
    stream_options: dict[str, bool] | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    user: str | None = None
    conversation_id: str | None = Field(default=None, alias="conversation_id")
    previous_response_id: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    response_format: dict[str, Any] | None = None
    stop: str | list[str] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    reasoning_effort: str | None = None


class ResponsesRequest(BaseModel):
    """Minimal text-only request contract for the Responses compatibility path."""

    model_config = ConfigDict(extra="allow")
    model: str = "deepseek-chat"
    input: Any
    conversation_id: ConversationId | None = None
    previous_response_id: ResponseId | None = None
    stream: bool = False



class ModelCapabilities(BaseModel):
    tool_calling: Literal["none", "emulated", "native"] = "emulated"
    streaming: Literal["buffered"] = "buffered"
    image_input: bool = False
    context_window: int | None = None
    max_output_tokens: int | None = None
    sampling_controls: bool = False
    parallel_tool_calls: bool = False
    reasoning: bool = False


class ProviderRequest(BaseModel):
    chat: ChatRequest
    canonical: CanonicalRequest | None = None
    conversation_id: ConversationId | None = None
    previous_response_id: ResponseId | None = None
    system_prompt: str = ""
    client_policy: ClientPolicy = ClientPolicy.GENERIC


class ProviderResult(BaseModel):
    response_id: ResponseId | None = None
    conversation_id: ConversationId | None = None
    content: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    finish_reason: Literal["stop", "tool_calls", "length", "content_filter"] = "stop"
    usage: dict[str, int] | None = None
