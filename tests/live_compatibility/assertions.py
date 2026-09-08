from __future__ import annotations

import json


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


def assert_sse_semantics(body: str) -> None:
    frames = [line[6:] for line in body.splitlines() if line.startswith("data: ")]
    assert frames and frames[-1] == "[DONE]"
    for frame in frames[:-1]:
        parsed = json.loads(frame)
        assert parsed.get("object") == "chat.completion.chunk"


def assert_safe_error(response: dict, status: int) -> None:
    assert status >= 400
    error = response.get("error") or {}
    assert error.get("type") in {"provider_error", "invalid_request_error"}
    assert isinstance(error.get("code"), str) and error["code"]
    assert "password" not in json.dumps(response).lower()
    assert "token" not in json.dumps(response).lower()
