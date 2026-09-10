"""Explicit streaming primitives for provider-observed incremental output.

The current web providers do not expose incremental observations, so these
primitives are intentionally not used to split completed DOM responses.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .contract import ResponseId


class ObservedDelta(BaseModel):
    """One provider-observed incremental fragment, never a fabricated token."""

    sequence: int = Field(ge=0)
    content: str | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_arguments: str | None = None

    @model_validator(mode="after")
    def validate_payload(self):
        if self.content is None and self.tool_arguments is None:
            raise ValueError("Observed delta must contain content or tool arguments")
        if self.tool_arguments is not None and not self.tool_call_id:
            raise ValueError("Tool argument delta requires tool_call_id")
        return self


class StreamChunk(BaseModel):
    """OpenAI Chat Completions-shaped chunk with explicit sequencing."""

    id: ResponseId
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    sequence: int = Field(ge=0)
    delta: dict[str, Any] = Field(default_factory=dict)
    finish_reason: Literal["stop", "tool_calls", "length", "content_filter"] | None = None
    error: dict[str, str] | None = None


def project_observed_deltas(
    response_id: ResponseId,
    model: str,
    created: int,
    deltas: Iterable[ObservedDelta],
    finish_reason: Literal["stop", "tool_calls", "length", "content_filter"] = "stop",
) -> Iterator[StreamChunk]:
    """Project ordered observed fragments and one terminal event.

    Sequence numbers are checked rather than rewritten, ensuring callers cannot
    mistake a reordered or missing provider observation for valid progress.
    """
    expected = 0
    for delta in deltas:
        if delta.sequence != expected:
            raise ValueError("Observed stream deltas must be contiguous and ordered")
        payload: dict[str, Any]
        if delta.content is not None:
            payload = {"content": delta.content}
        else:
            payload = {"tool_calls": [{
                "index": 0,
                "id": delta.tool_call_id,
                "type": "function",
                "function": {
                    **({"name": delta.tool_name} if delta.tool_name else {}),
                    "arguments": delta.tool_arguments,
                },
            }]}
        yield StreamChunk(
            id=response_id, model=model, created=created, sequence=expected + 1, delta=payload
        )
        expected += 1
    yield StreamChunk(
        id=response_id,
        model=model,
        created=created,
        sequence=expected + 1,
        finish_reason=finish_reason,
    )


def error_chunk(
    response_id: ResponseId, model: str, created: int, sequence: int, code: str, message: str
) -> StreamChunk:
    """Create a safe terminal error event without provider internals."""
    return StreamChunk(
        id=response_id,
        model=model,
        created=created,
        sequence=sequence,
        error={"code": code, "message": message},
    )
