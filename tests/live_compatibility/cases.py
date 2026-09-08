from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    case_id: str
    group: str
    title: str
    goal: str
    preconditions: str
    method: str
    expected: str
    request: dict


_GROUPS = (
    ("contract", "Contract / shape", (
        "plain text completion", "response envelope and required fields", "opaque id format and uniqueness",
        "created integer and model echo", "assistant role/index/finish_reason", "content type and null/empty handling",
        "system message", "user/assistant message ordering", "Unicode/Persian and emoji", "quotes, escapes, and multiline content",
    )),
    ("normalization", "Semantic normalization", (
        "Markdown", "code block", "JSON text", "HTML-like text preserved as text", "multiple code blocks",
        "long response", "empty response normalization", "response content is not silently rewritten",
    )),
    ("tools", "Tool calling", (
        "tools schema accepted", "tool_choice auto", "tool_choice none", "tool_choice required", "named tool choice",
        "tool call structure/id/type/name", "valid JSON arguments", "nested/complex arguments", "tool result continuation",
        "tool_call_id preservation", "malformed tool output controlled error", "unknown/duplicate tool call controlled behavior",
    )),
    ("conversation", "Conversation fidelity", (
        "multi-turn memory", "system instruction persistence", "assistant history", "tool history", "conversation isolation",
        "conversation reset/new conversation", "simultaneous conversations", "long conversation/page reuse",
    )),
    ("streaming-errors", "Streaming and errors", (
        "stream=true SSE framing", "valid JSON SSE chunks", "chunk ordering and [DONE]", "streamed reconstruction",
        "stream/non-stream contract equivalence", "invalid model", "malformed JSON/missing messages",
        "invalid roles/tools schema", "provider timeout/DOM change/login expiry safe error", "unavailable/rate-limit/empty-response safe API errors",
    )),
    ("golden", "Golden compatibility", (
        "OpenAI SDK black-box deserialization/authentication", "OpenClaw normal prompt and tool-loop smoke path",
    )),
)


def _request(title: str) -> dict:
    request = {"model": "deepseek-chat", "messages": [{"role": "user", "content": f"compatibility case: {title}"}]}
    if "tool" in title.lower():
        request["tools"] = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]
    if "stream" in title.lower() or "SSE" in title:
        request["stream"] = True
    return request


def _build_cases() -> tuple[Case, ...]:
    cases = []
    number = 1
    for group, _label, titles in _GROUPS:
        for title in titles:
            cases.append(Case(
                f"T{number:02d}", group, title, f"Verify {title} without comparing generated answer text.",
                "Running local MimicGate endpoint with an authenticated isolated provider profile.",
                "Send the canonical OpenAI-shaped request and apply semantic assertions for this category.",
                "HTTP/schema/semantic behavior matches the documented OpenAI-compatible contract, or a controlled safe error is returned.",
                _request(title),
            ))
            number += 1
    assert len(cases) == 50
    return tuple(cases)


CASES = _build_cases()
GROUPS = {group: label for group, label, _ in _GROUPS}
