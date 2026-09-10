from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any, Literal, NewType, TypeAlias
from pydantic import BaseModel, ConfigDict, Field, model_validator
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


class StructuredOutputSpec(BaseModel):
    """Validated JSON output request with its caller-supplied schema metadata."""

    type: Literal["json_object", "json_schema"]
    name: str | None = None
    schema: dict[str, Any] | None = None
    strict: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


_SUPPORTED_SCHEMA_KEYS = {
    "type", "properties", "required", "additionalProperties", "items",
    "enum", "const", "description", "title", "default",
}
_SUPPORTED_SCHEMA_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def _validate_schema(schema: Any, path: str = "schema") -> None:
    if not isinstance(schema, dict):
        raise ValueError(f"{path} must be an object")
    unsupported = sorted(set(schema) - _SUPPORTED_SCHEMA_KEYS)
    if unsupported:
        raise ValueError(f"Unsupported JSON schema feature: {unsupported[0]}")
    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in _SUPPORTED_SCHEMA_TYPES:
        raise ValueError(f"Unsupported JSON schema type: {schema_type}")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError(f"{path}.properties must be an object")
    for name, child in properties.items():
        if not isinstance(name, str):
            raise ValueError(f"{path}.properties names must be strings")
        _validate_schema(child, f"{path}.properties.{name}")
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
        raise ValueError(f"{path}.required must be a list of strings")
    if any(name not in properties for name in required):
        raise ValueError(f"{path}.required names must exist in properties")
    additional = schema.get("additionalProperties", True)
    if not isinstance(additional, bool):
        raise ValueError(f"{path}.additionalProperties must be boolean")
    if "items" in schema:
        _validate_schema(schema["items"], f"{path}.items")


def normalize_structured_output(
    response_format: dict[str, Any] | None = None,
    text_format: dict[str, Any] | None = None,
) -> StructuredOutputSpec | None:
    """Normalize the two supported public JSON output shapes."""
    if response_format is not None and text_format is not None:
        raise ValueError("Specify only one structured output format")
    value = response_format if response_format is not None else text_format
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("Structured output format must be an object")
    kind = value.get("type")
    if kind == "json_object":
        allowed = {"type"}
        extra = sorted(set(value) - allowed)
        if extra:
            raise ValueError(f"Unsupported JSON object feature: {extra[0]}")
        return StructuredOutputSpec(type="json_object", metadata=dict(value))
    if kind != "json_schema":
        raise ValueError("Only json_object and json_schema output formats are supported")
    schema_value = value.get("json_schema", value)
    if not isinstance(schema_value, dict):
        raise ValueError("json_schema must be an object")
    allowed = {"type", "json_schema"}
    if "json_schema" in value:
        metadata = dict(schema_value)
    else:
        metadata = dict(value)
    name = metadata.get("name")
    schema = metadata.get("schema")
    if not isinstance(name, str) or not name:
        raise ValueError("JSON schema name must be nonempty")
    _validate_schema(schema)
    strict = metadata.get("strict")
    if strict is not None and not isinstance(strict, bool):
        raise ValueError("JSON schema strict must be boolean")
    return StructuredOutputSpec(type="json_schema", name=name, schema=schema, strict=strict, metadata=metadata)


def validate_structured_output(text: str, spec: StructuredOutputSpec) -> Any:
    """Parse and validate structured output without mutating or truncating it."""
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Provider output is not valid JSON") from exc
    if spec.type == "json_object" and not isinstance(value, dict):
        raise ValueError("Provider output must be a JSON object")
    if spec.schema is not None:
        _validate_json_value(value, spec.schema, "output")
    return value


def _validate_json_value(value: Any, schema: dict[str, Any], path: str) -> None:
    expected = schema.get("type")
    valid = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if expected and not valid[expected]:
        raise ValueError(f"Provider output does not match {path} type")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"Provider output does not match {path} enum")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"Provider output does not match {path} const")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                raise ValueError(f"Provider output is missing {path}.{name}")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise ValueError(f"Provider output has unsupported field {path}.{extra[0]}")
        for name, child in properties.items():
            if name in value:
                _validate_json_value(value[name], child, f"{path}.{name}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _validate_json_value(item, schema["items"], f"{path}[{index}]")


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
    structured_output: StructuredOutputSpec | None = None


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
        structured_output=normalize_structured_output(request.response_format),
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


class LegacyCompletionRequest(BaseModel):
    """Supported subset of the legacy text completions request shape."""
    model_config = ConfigDict(extra="allow")
    model: str = "deepseek-chat"
    prompt: Any
    stream: bool = False
    user: str | None = None


class ResponsesRequest(BaseModel):
    """Minimal text-only request contract for the Responses compatibility path."""

    model_config = ConfigDict(extra="allow")
    model: str = "deepseek-chat"
    input: Any
    conversation_id: ConversationId | None = None
    previous_response_id: ResponseId | None = None
    stream: bool = False
    text: dict[str, Any] | None = None



class ModelCapabilities(BaseModel):
    tool_calling: Literal["none", "emulated", "native"] = "emulated"
    streaming: Literal["buffered"] = "buffered"
    image_input: bool = False
    context_window: int | None = None
    max_output_tokens: int | None = None
    sampling_controls: bool = False
    parallel_tool_calls: bool = False
    reasoning: bool = False
    gateway_max_input_chars: int | None = None
    gateway_max_output_chars: int | None = None


class ProviderRequest(BaseModel):
    chat: ChatRequest
    canonical: CanonicalRequest | None = None
    conversation_id: ConversationId | None = None
    previous_response_id: ResponseId | None = None
    structured_output: StructuredOutputSpec | None = None
    system_prompt: str = ""
    client_policy: ClientPolicy = ClientPolicy.GENERIC


class ProviderResult(BaseModel):
    response_id: ResponseId | None = None
    conversation_id: ConversationId | None = None
    content: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    finish_reason: Literal["stop", "tool_calls", "length", "content_filter"] = "stop"
    usage: dict[str, int] | None = None

    @model_validator(mode="after")
    def validate_finish_semantics(self):
        if self.tool_calls and self.finish_reason != "tool_calls":
            raise ValueError("tool_calls require finish_reason=tool_calls")
        if self.finish_reason == "tool_calls" and not self.tool_calls:
            raise ValueError("finish_reason=tool_calls requires tool_calls")
        return self
