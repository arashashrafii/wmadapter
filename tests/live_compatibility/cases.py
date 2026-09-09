from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

TOOLS = [{"type": "function", "function": {"name": "lookup", "description": "fixture lookup", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}]


@dataclass(frozen=True)
class Case:
    case_id: str
    group: str
    title: str
    goal: str
    preconditions: str
    method: str
    expected: str
    payload_builder: Callable[[str], dict]
    expected_status: int = 200
    stream: bool = False

    def payload(self, model: str) -> dict:
        return self.payload_builder(model)


GROUPS = {"contract": "Contract / shape", "normalization": "Semantic normalization", "tools": "Tool calling", "conversation": "Conversation fidelity", "streaming-errors": "Streaming and errors", "golden": "Golden compatibility"}


def _builder(case_id: str, messages=None, **extra):
    def build(model: str) -> dict:
        payload = {"model": model, "messages": messages or [{"role": "user", "content": f"compatibility fixture {case_id}"}]}
        payload.update(extra)
        return payload
    build.__name__ = f"build_{case_id.lower()}"
    return build


def _tool_builder(case_id: str, choice=None, messages=None, **extra):
    values = {"tools": TOOLS, **extra}
    if choice is not None: values["tool_choice"] = choice
    return _builder(case_id, messages=messages, **values)


def _make(case_id, group, title, builder, expected_status=200, stream=False):
    return Case(case_id, group, title, f"Verify {title} using protocol semantics only.", "Configured local endpoint and isolated provider profile; live readiness is required only for live execution.", f"Build and send the case-specific {case_id} payload, then apply semantic assertions.", "The documented semantic result or exact controlled error is observed.", builder, expected_status, stream)


_SPECS = [
    ("contract", ["plain text completion", "response envelope and required fields", "opaque id format and uniqueness", "created integer and model echo", "assistant role/index/finish_reason", "content type and null/empty handling", "system message", "user/assistant message ordering", "Unicode/Persian and emoji", "quotes, escapes, and multiline content"]),
    ("normalization", ["Markdown", "code block", "JSON text", "HTML-like text preserved as text", "multiple code blocks", "long response", "empty response normalization", "response content is not silently rewritten"]),
    ("tools", ["tools schema accepted", "tool_choice auto", "tool_choice none", "tool_choice required", "named tool choice", "tool call structure/id/type/name", "valid JSON arguments", "nested/complex arguments", "tool result continuation", "tool_call_id preservation", "malformed tool output controlled error", "unknown/duplicate tool call controlled behavior"]),
    ("conversation", ["multi-turn memory", "system instruction persistence", "assistant history", "tool history", "conversation isolation", "conversation reset/new conversation", "simultaneous conversations", "long conversation/page reuse"]),
    ("streaming-errors", ["stream=true SSE framing", "valid JSON SSE chunks", "chunk ordering and [DONE]", "streamed reconstruction", "stream/non-stream contract equivalence", "invalid model", "malformed JSON/missing messages", "invalid roles/tools schema", "provider timeout/DOM change/login expiry safe error", "unavailable/rate-limit/empty-response safe API errors"]),
    ("golden", ["OpenAI SDK black-box deserialization/authentication", "OpenClaw normal prompt and tool-loop smoke path"]),
]


def _build_all():
    result = []
    number = 1
    for group, titles in _SPECS:
        for title in titles:
            cid, lower = f"T{number:02d}", title.lower()
            builder, status, stream = _builder(cid), 200, False
            if "system message" in lower: builder = _builder(cid, messages=[{"role": "system", "content": "Answer with protocol evidence only."}, {"role": "user", "content": "hello"}])
            elif "ordering" in lower: builder = _builder(cid, messages=[{"role": "user", "content": "first"}, {"role": "assistant", "content": "ack"}, {"role": "user", "content": "second"}])
            elif "unicode" in lower: builder = _builder(cid, messages=[{"role": "user", "content": "فارسی 🚀 — café"}])
            elif "quotes" in lower: builder = _builder(cid, messages=[{"role": "user", "content": '"quoted"\\nline\\t\\\\'}])
            elif "markdown" in lower: builder = _builder(cid, messages=[{"role": "user", "content": "Return **bold** and _italic_ text."}])
            elif "multiple code" in lower: builder = _builder(cid, messages=[{"role": "user", "content": "Return ```python\\nprint(1)\\n``` and ```json\\n{}\\n``` unchanged."}])
            elif "code block" in lower: builder = _builder(cid, messages=[{"role": "user", "content": "Return ```python\\nprint(1)\\n``` as text."}])
            elif "json text" in lower: builder = _builder(cid, messages=[{"role": "user", "content": '{"answer": true, "items": [1, 2]}' }])
            elif "html-like" in lower: builder = _builder(cid, messages=[{"role": "user", "content": "Keep <tag>literal</tag> as text."}])
            elif group == "conversation": builder = _builder(cid, messages=[{"role": "user", "content": f"conversation {cid}"}], conversation_id=f"compat-{cid.lower()}")
            if group == "tools":
                choice = "auto" if "tool_choice auto" in lower else "none" if "tool_choice none" in lower else "required" if "required" in lower else {"type": "function", "function": {"name": "lookup"}} if "named" in lower else None
                builder = _tool_builder(cid, choice)
                if "result" in lower or "tool_call_id" in lower:
                    call_id = f"call_{cid.lower()}"
                    messages = [{"role": "user", "content": "lookup"}, {"role": "assistant", "tool_calls": [{"id": call_id, "type": "function", "function": {"name": "lookup", "arguments": "{\"q\":\"x\"}"}}]}, {"role": "tool", "tool_call_id": call_id, "content": "fixture-result"}]
                    builder = _tool_builder(cid, messages=messages)
            if "stream" in lower or "sse" in lower or "reconstruction" in lower or "chunk" in lower: stream, builder = True, _builder(cid, stream=True)
            if "invalid model" in lower: builder, status = _builder(cid, model="invalid-model"), 404
            if "malformed json" in lower: builder, status = _builder(cid, messages=[]), 400
            if "invalid roles" in lower: builder, status = _builder(cid, messages=[{"role": "invalid", "content": "x"}]), 400
            if "malformed tool" in lower:
                builder = _tool_builder(cid, messages=[
                    {"role": "user", "content": "lookup"},
                    {"role": "assistant", "tool_calls": [{"id": f"call_{cid.lower()}", "type": "function", "function": {"name": "lookup", "arguments": "{bad-json"}}]},
                    {"role": "tool", "tool_call_id": "unknown-call", "content": "fixture-result"},
                ])
                status = 400
            if "unknown/duplicate" in lower:
                duplicate_tools = [*TOOLS, {"type": "function", "function": {"name": "lookup", "description": "duplicate", "parameters": {"type": "object"}}}]
                builder = _builder(cid, tools=duplicate_tools)
                status = 400
            result.append(_make(cid, group, title, builder, status, stream)); number += 1
    assert len(result) == 50
    return tuple(result)


CASES = _build_all()
