from __future__ import annotations

import json
from .transport import SSEParser


def assert_completion_semantics(response: dict, model: str) -> None:
    assert response.get("object") == "chat.completion"
    assert response.get("model") == model
    assert isinstance(response.get("id"), str) and response["id"].startswith("chatcmpl-")
    assert isinstance(response.get("created"), int)
    choices = response.get("choices")
    assert isinstance(choices, list) and choices
    choice = choices[0]
    assert choice.get("index") == 0
    assert choice.get("finish_reason") in {"stop", "tool_calls"}
    message = choice.get("message") or {}
    assert message.get("role") == "assistant"
    assert message.get("content") is None or isinstance(message.get("content"), str)


def assert_sse_semantics(body: str, *, expected_classification: str | None = None) -> str:
    parser = SSEParser(); frames = []
    for start in range(0, len(body), 7): frames.extend(parser.feed(body[start:start + 7]))
    frames.extend(parser.finish())
    assert frames and frames[-1] == "[DONE]"
    assert frames.count("[DONE]") == 1
    chunks = [json.loads(frame) for frame in frames[:-1]]
    assert chunks and all(chunk.get("object") == "chat.completion.chunk" for chunk in chunks)
    assert all(chunk.get("choices") for chunk in chunks)
    assert chunks[0]["choices"][0]["delta"].get("role") == "assistant"
    assert chunks[-1]["choices"][0].get("finish_reason") == "stop"
    content = [chunk["choices"][0]["delta"].get("content") for chunk in chunks if chunk["choices"][0]["delta"].get("content") is not None]
    classification = "progressive" if len(content) > 1 else "buffered"
    if expected_classification is not None: assert classification == expected_classification
    return classification


def assert_tool_roundtrip(response: dict, model: str) -> str:
    assert_completion_semantics(response, model)
    choice = response["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    calls = (choice.get("message") or {}).get("tool_calls") or []
    assert len(calls) == 1
    call = calls[0]
    assert call.get("type") == "function"
    function = call.get("function") or {}
    assert function.get("name") == "lookup"
    arguments = json.loads(function.get("arguments", ""))
    assert arguments.get("q") == "fixture-nonce"
    return call["id"]


def assert_safe_error(response: dict, status: int) -> None:
    assert status >= 400
    error = response.get("error") or {}
    assert error.get("type") in {"provider_error", "invalid_request_error"}
    assert isinstance(error.get("code"), str) and error["code"]
    assert "password" not in json.dumps(response).lower()
    assert "token" not in json.dumps(response).lower()
