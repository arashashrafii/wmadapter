from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from .policy import ClientPolicy

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
    conversation_id: str | None = None


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
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    response_format: dict[str, Any] | None = None
    stop: str | list[str] | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    seed: int | None = None
    reasoning_effort: str | None = None



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
    conversation_id: str | None = None
    system_prompt: str = ""
    client_policy: ClientPolicy = ClientPolicy.GENERIC


class ProviderResult(BaseModel):
    content: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    finish_reason: Literal["stop", "tool_calls", "length", "content_filter"] = "stop"
    usage: dict[str, int] | None = None
