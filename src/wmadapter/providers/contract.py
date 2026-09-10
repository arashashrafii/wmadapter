from __future__ import annotations

from collections.abc import Mapping, Sequence
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


class CanonicalToolCall(BaseModel):
    """Normalized function call representation; calls remain data, never execution."""

    id: str = Field(min_length=1)
    type: Literal["function"] = "function"
    function: dict[str, Any]


class CanonicalToolResult(BaseModel):
    """Normalized result associated with one previously declared tool call."""

    tool_call_id: str = Field(min_length=1)
    content: Any = ""


def normalize_tool_calls(calls: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate and normalize calls while preserving their declared order."""
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for call in calls:
        try:
            item = CanonicalToolCall.model_validate(call)
        except Exception as exc:
            raise ValueError("Malformed tool call") from exc
        if item.id in seen:
            raise ValueError("Duplicate tool call id")
        if not isinstance(item.function.get("name"), str) or not item.function["name"]:
            raise ValueError("Tool calls require a nonempty function name")
        if not isinstance(item.function.get("arguments"), str):
            raise ValueError("Tool calls require string arguments")
        seen.add(item.id)
        normalized.append(item.model_dump())
    return normalized


def normalize_tool_results(
    results: Sequence[Mapping[str, Any]], expected_ids: Sequence[str]
) -> list[dict[str, Any]]:
    """Validate results against call IDs, retaining the caller's result order."""
    expected = list(expected_ids)
    if len(set(expected)) != len(expected):
        raise ValueError("Duplicate expected tool call id")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for result in results:
        try:
            item = CanonicalToolResult.model_validate(result)
        except Exception as exc:
            raise ValueError("Malformed tool result") from exc
        if item.tool_call_id in seen:
            raise ValueError("Duplicate tool result id")
        if item.tool_call_id not in expected:
            raise ValueError("Unknown tool result id")
        seen.add(item.tool_call_id)
        normalized.append(item.model_dump())
    missing = [call_id for call_id in expected if call_id not in seen]
    if missing:
        raise ValueError("Missing tool result")
    return normalized


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
