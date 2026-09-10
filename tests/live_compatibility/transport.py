from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class TransportResponse:
    status: int
    content_type: str
    body: str


class SSEParser:
    """Incremental SSE parser tolerant of LF, CRLF, CR and split chunks."""
    def __init__(self):
        self._buffer, self._data = "", []

    def feed(self, chunk: str) -> list[str]:
        self._buffer += chunk
        events = []
        while True:
            index = next((i for i, c in enumerate(self._buffer) if c in "\r\n"), None)
            if index is None: break
            end = index + 1
            if self._buffer[index] == "\r" and end < len(self._buffer) and self._buffer[end] == "\n": end += 1
            line, self._buffer = self._buffer[:index], self._buffer[end:]
            if line.startswith("data:"): self._data.append(line[5:].lstrip())
            elif not line and self._data:
                events.append("\n".join(self._data)); self._data = []
        return events

    def finish(self) -> list[str]:
        events = self.feed("\n") if self._buffer else []
        if self._data: events.append("\n".join(self._data)); self._data = []
        return events


class FixtureTransport:
    def request(self, case, payload: dict) -> TransportResponse:
        if case.expected_status != 200:
            code = {400: "invalid_request", 404: "model_not_found"}.get(case.expected_status, "provider_error")
            return TransportResponse(case.expected_status, "application/json", json.dumps({"error": {"type": "invalid_request_error", "code": code, "message": "fixture error"}}))
        if case.stream:
            body = (
                'data: {"object":"chat.completion.chunk","id":"chatcmpl-fixture",'
                '"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
                'data: {"object":"chat.completion.chunk","id":"chatcmpl-fixture",'
                '"choices":[{"index":0,"delta":{"content":"fixture"},"finish_reason":null}]}\n\n'
                'data: {"object":"chat.completion.chunk","id":"chatcmpl-fixture",'
                '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                'data: [DONE]\n\n'
            )
            return TransportResponse(200, "text/event-stream", body)
        if getattr(case, "kind", "") == "tool_roundtrip":
            if any(message.get("role") == "tool" for message in payload.get("messages", [])):
                body = {"id": f"chatcmpl-{case.case_id.lower()}-final", "object": "chat.completion", "created": 1,
                        "model": payload["model"], "choices": [{"index": 0, "message": {"role": "assistant", "content": "fixture-nonce confirmed"}, "finish_reason": "stop"}], "usage": None}
                return TransportResponse(200, "application/json", json.dumps(body))
            body = {"id": f"chatcmpl-{case.case_id.lower()}", "object": "chat.completion", "created": 1,
                    "model": payload["model"], "choices": [{"index": 0, "message": {"role": "assistant", "content": None,
                    "tool_calls": [{"id": "call_fixture_nonce", "type": "function", "function": {"name": "lookup", "arguments": "{\"q\":\"fixture-nonce\"}"}}]}, "finish_reason": "tool_calls"}], "usage": None}
            return TransportResponse(200, "application/json", json.dumps(body))
        body = {"id": f"chatcmpl-{case.case_id.lower()}", "object": "chat.completion", "created": 1, "model": payload["model"], "choices": [{"index": 0, "message": {"role": "assistant", "content": "fixture"}, "finish_reason": "stop"}], "usage": None}
        return TransportResponse(200, "application/json", json.dumps(body))
