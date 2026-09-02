from __future__ import annotations

import json
import hashlib
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .config import load_config
from .logging import configure_logging
from .providers.router import ProviderRouter
from .security import redact
from .service import DeepSeekService, QwenService

config = load_config()
configure_logging(config["logging"])
logger = logging.getLogger(__name__)
providers = {"deepseek": DeepSeekService(config), "qwen": QwenService(config)}
router = ProviderRouter(providers, config["providers"]["default"])
default_system_prompt = config["deepseek"].get("system_prompt", "")


class Message(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Any = ""
    name: str | None = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str = "deepseek-chat"
    messages: list[Message]
    stream: bool = False
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


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                value = item.get("text") or item.get("content")
                if isinstance(value, str):
                    parts.append(value)
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _prompt(messages: list[Message], system_prompt: str = "", tools: list[dict[str, Any]] | None = None) -> str:
    lines = []
    if system_prompt.strip() and not any(message.role.lower() == "system" for message in messages):
        lines.append(f"SYSTEM: {system_prompt.strip()}")
    for message in messages:
        text = _content_text(message.content).strip()
        if message.role.lower() == "assistant" and getattr(message, "tool_calls", None):
            for call in message.tool_calls:
                function = call.get("function", {}) if isinstance(call, dict) else {}
                lines.append("ASSISTANT_TOOL_CALL: " + json.dumps({
                    "name": function.get("name", ""),
                    "arguments": function.get("arguments", "{}"),
                }, ensure_ascii=False))
        elif message.role.lower() == "tool":
            lines.append(f"TOOL_RESULT ({getattr(message, 'tool_call_id', '')}): {text}")
        elif text:
            lines.append(f"{message.role.upper()}: {text}")
    if tools:
        tool_text = json.dumps(tools, ensure_ascii=False, separators=(",", ":"))
        lines.append(
            "FINAL TOOL PROTOCOL: You may use one listed tool. If a tool is required, output only "
            "<tool_call>{\"name\":\"tool_name\",\"arguments\":{...}}</tool_call>. "
            "Do not describe a tool call and do not execute commands in text. "
            "Available tools: " + tool_text
        )
        lines.append(
            "OPENCLAW CAPABILITY POLICY: Treat the listed tool schemas as the only callable capabilities. "
            "Use the tool whose description best matches the user's goal: use browser for browser/web automation, "
            "read/write/edit/apply_patch for files, exec/process for system work, and session/agent tools for delegation. "
            "Skills provide workflow instructions and plugins provide extra tools; use them when a matching capability "
            "is listed or its instructions are present. Do not invent plugin or skill behavior. Never claim that a task "
            "was completed before receiving its tool result. After a tool result, continue the workflow or give the "
            "final answer. Keep tool protocol markers and code-renderer labels out of the final answer."
        )
        if any(item.get("function", {}).get("name") == "browser" for item in tools):
            lines.append(
                "BROWSER TOOL SHAPE: For browser action=act and kind=fill, always send "
                "fields:[{ref:<textbox ref>,text:<value>}]. Never send fill ref/text as top-level fields."
            )
        if any(message.role.lower() == "tool" for message in messages):
            lines.append(
                "FINAL TOOL PROTOCOL: A tool result is already available above. "
                "Use it to answer the user; call another tool only if the result is insufficient."
            )
    return "\n\n".join(lines)


def _is_title_request(messages: list[Message]) -> bool:
    for message in messages:
        if message.role.lower() != "system":
            continue
        text = _content_text(message.content).lower()
        if "generate a concise session title" in text:
            return True
    return False


def _local_title(messages: list[Message]) -> str:
    for message in messages:
        if message.role.lower() != "user":
            continue
        text = " ".join(_content_text(message.content).split())
        if text:
            return text[:60].rstrip()
    return "New conversation"


def _fallback_conversation_id(messages: list[Message]) -> str | None:
    """Create a stable key for clients that omit OpenClaw session headers."""
    for message in messages:
        if message.role.lower() != "user":
            continue
        text = " ".join(_content_text(message.content).split())
        if text:
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
            return f"auto:{digest}"
    return None


def _fallback_after_tool(messages: list[Message], answer: str) -> str:
    """Keep OpenClaw from receiving an empty assistant turn after a tool."""
    if answer.strip():
        return answer
    tool_activity = False
    for message in reversed(messages):
        role = message.role.lower()
        if role == "tool" or (role == "assistant" and getattr(message, "tool_calls", None)):
            tool_activity = True
        if role in {"tool", "tool_result"}:
            result = _content_text(message.content).strip()
            if result:
                return f"Tool result:\n{result}"
    return "The requested tool completed, but no output was returned." if tool_activity else answer


def _clean_renderer_artifacts(answer: str) -> str:
    """Remove labels injected by DeepSeek Web's code-block renderer."""
    lines = answer.splitlines()
    cleaned: list[str] = []
    index = 0
    while index < len(lines):
        labels = [line.strip().lower() for line in lines[index:index + 3]]
        if labels == ["text", "copy", "download"]:
            index += 3
            continue
        cleaned.append(lines[index])
        index += 1
    return "\n".join(cleaned).strip()


def _normalize_tool_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Repair one known OpenClaw browser fill shape without broad coercion."""
    if (
        name == "browser"
        and arguments.get("action") == "act"
        and arguments.get("kind") == "fill"
        and "fields" not in arguments
        and isinstance(arguments.get("ref"), str)
        and isinstance(arguments.get("text"), str)
    ):
        normalized = dict(arguments)
        ref = normalized.pop("ref")
        text = normalized.pop("text")
        normalized["fields"] = [{"ref": ref, "text": text}]
        return normalized
    return arguments


def _extract_tool_call(answer: str, tools: list[dict[str, Any]] | None) -> tuple[dict[str, Any] | None, str]:
    if not tools:
        return None, answer
    match = re.search(r"<tool_call>\s*(\{.*\})\s*</tool_call>", answer, re.DOTALL)
    json_match = None
    if not match:
        legacy = re.search(r'<invoke\s+name=["\']([^"\']+)["\']>(.*?)</invoke>', answer, re.DOTALL)
        if not legacy:
            # DeepSeek Web occasionally formats a requested tool call as a
            # Markdown JSON code block instead of emitting our XML-like
            # marker. Accept only an object with the exact tool-call shape;
            # ordinary JSON answers remain visible text.
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", answer, re.IGNORECASE | re.DOTALL)
            if not json_match:
                # The DeepSeek Web renderer can strip the code-fence and
                # leave labels such as "json / Copy / Download" before the
                # object. Scan JSON objects and accept only the exact shape
                # used for a tool call.
                decoder = json.JSONDecoder()
                for offset in (m.start() for m in re.finditer(r"\{", answer)):
                    try:
                        candidate, _ = decoder.raw_decode(answer[offset:])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(candidate, dict) and "name" in candidate and "arguments" in candidate:
                        call = candidate
                        match_start = offset
                        break
                else:
                    return None, answer
            else:
                try:
                    call = json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    return None, answer
                match_start = json_match.start()
        else:
            name = legacy.group(1)
            arguments = {
                key: value.strip()
                for key, value in re.findall(r'<parameter\s+name=["\']([^"\']+)["\']>(.*?)</parameter>', legacy.group(2), re.DOTALL)
            }
            call = {"name": name, "arguments": arguments}
            match_start = answer.rfind("<function_calls>", 0, legacy.start())
            if match_start < 0:
                match_start = legacy.start()
    else:
        match_start = match.start()
    try:
        if match and not json_match:
            payload = match.group(1)
            try:
                call = json.loads(payload)
            except json.JSONDecodeError:
                # Some DeepSeek Web responses quote the arguments object but
                # fail to escape its inner quotes, e.g.
                # {"name":"exec","arguments":"{"command":"pwd"}"}.
                # Recover only that explicit tool-call shape; never infer a
                # tool from a plain Bash/code block.
                name_match = re.search(r'"name"\s*:\s*"([^"\\]+)"', payload)
                arguments_pos = re.search(r'"arguments"\s*:\s*', payload)
                if not name_match or not arguments_pos:
                    return None, answer
                object_start = payload.find("{", arguments_pos.end())
                if object_start < 0:
                    return None, answer
                try:
                    call_arguments, _ = json.JSONDecoder().raw_decode(payload[object_start:])
                except json.JSONDecodeError:
                    # The model may also omit escaping quotes inside a
                    # string argument (most often write.content or
                    # exec.command). Recover the known OpenClaw argument
                    # fields conservatively, without executing arbitrary
                    # prose as a command.
                    def string_argument(field: str, end_fields: tuple[str, ...] = ()) -> str | None:
                        field_match = re.search(rf'"{re.escape(field)}"\s*:\s*"', payload[object_start:])
                        if not field_match:
                            return None
                        start = object_start + field_match.end()
                        end = len(payload)
                        for end_field in end_fields:
                            next_field = re.search(rf'"{re.escape(end_field)}"\s*:', payload[start:])
                            if next_field:
                                end = min(end, start + next_field.start() - 1)
                        if end == len(payload):
                            closing = payload.rfind('"}}')
                            end = closing if closing >= start else payload.rfind('"}')
                        if end < start:
                            return None
                        value = payload[start:end]
                        return value.replace(r"\n", "\n").replace(r"\r", "\r").replace(r"\t", "\t").replace(r"\\", "\\")

                    if name_match.group(1) == "write":
                        path = string_argument("path", ("content",))
                        content = string_argument("content")
                        if path is None or content is None:
                            return None, answer
                        call_arguments = {"path": path.rstrip('" ,'), "content": content}
                    elif name_match.group(1) == "exec":
                        command = string_argument("command", ("yieldMs", "timeout"))
                        if command is None:
                            return None, answer
                        call_arguments = {"command": command}
                        for numeric_field in ("yieldMs", "timeout"):
                            numeric_match = re.search(rf'"{numeric_field}"\s*:\s*(\d+)', payload[object_start:])
                            if numeric_match:
                                call_arguments[numeric_field] = int(numeric_match.group(1))
                    else:
                        return None, answer
                call = {"name": name_match.group(1), "arguments": call_arguments}
        name = call.get("name")
        arguments = call.get("arguments", {})
        allowed = {item.get("function", {}).get("name") for item in (tools or [])}
        if not isinstance(name, str) or (allowed and name not in allowed):
            return None, answer
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        if not isinstance(arguments, dict):
            return None, answer
        arguments = _normalize_tool_arguments(name, arguments)
        return {"id": f"call_{uuid.uuid4().hex}", "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
        }}, answer[:match_start].strip()
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, answer


def _completion_response(request_id: str, model: str, answer: str, tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": answer}
    finish_reason = "stop"
    if tool_calls:
        message["content"] = None
        message["tool_calls"] = tool_calls
        finish_reason = "tool_calls"
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def _sse(data: dict[str, Any] | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
    return f"data: {payload}\n\n"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await router.start()
    except Exception as exc:
        providers["deepseek"].last_error = str(exc)
        logger.warning("Startup provider readiness check failed: %s", exc)
    yield
    await router.stop()


app = FastAPI(title="WebBridgeFreeRide", version="0.5.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    status = await router.status()
    enabled = config["providers"].get("enabled", [config["providers"]["default"]])
    ready_value = all(status.get(name, {}).get("ready") for name in enabled)
    return {"status": "ready" if ready_value else "not_ready", "providers": redact(status)}


@app.get("/props")
async def props(model: str, autoload: bool = False):
    return {"model": model, "context_length": 128000, "supports_chat": True}


@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [
            {"id": "deepseek-chat", "object": "model", "owned_by": "deepseek-web"},
            {"id": "deepseek-reasoner", "object": "model", "owned_by": "deepseek-web"},
            {"id": "qwen-chat", "object": "model", "owned_by": "qwen-web"},
        ],
    }


@app.delete("/v1/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, model: str = "deepseek-chat"):
    """Release the browser page associated with an OpenClaw session.

    This endpoint intentionally does not claim to delete DeepSeek's remote
    history: DeepSeek exposes that operation only through its web UI.
    """
    provider = router.provider_for_model(model)
    deleted = await provider.delete_conversation(conversation_id)
    return {"object": "conversation", "id": conversation_id, "deleted": deleted}


@app.post("/v1/chat/completions")
async def chat_completion(payload: ChatRequest, request: Request):
    request_id = f"chatcmpl-{uuid.uuid4().hex}"
    provider = router.provider_for_model(payload.model)
    logger.info("Chat request model=%s stream=%s tools=%s messages=%s", payload.model, payload.stream, len(payload.tools or []), len(payload.messages))
    # OpenClaw asks the model to generate a session title. Sending that
    # request through the same persistent web page leaves the title-only
    # instruction active in DeepSeek and contaminates the real conversation.
    # Titles are metadata, so handle them locally and keep them out of the
    # provider conversation entirely.
    if _is_title_request(payload.messages):
        return _completion_response(request_id, payload.model, _local_title(payload.messages))
    system_prompt = "" if provider.name == "qwen" else default_system_prompt
    prompt = _prompt(payload.messages, system_prompt, payload.tools if payload.tool_choice != "none" else None)
    if not prompt:
        raise HTTPException(status_code=400, detail="messages must contain text")
    # OpenAI-compatible clients differ in whether they send conversation_id.
    # Use the optional user field as a stable per-agent session key when it is
    # available, while retaining one persistent default page for simple clients.
    # OpenClaw carries its logical session in this header when calling an
    # external OpenAI-compatible provider. Prefer it over the optional body
    # fields so each OpenClaw session gets its own DeepSeek page.
    conversation_id = (
        request.headers.get("x-openclaw-session-key")
        or request.headers.get("x-openclaw-session-id")
        or payload.conversation_id
        or payload.user
        or _fallback_conversation_id(payload.messages)
    )

    if payload.stream:
        async def events():
            try:
                answer = ""
                async for chunk in provider.stream_complete(prompt, conversation_id=conversation_id):
                    answer += chunk
                tool_call, visible_answer = _extract_tool_call(answer, payload.tools if payload.tool_choice != "none" else None)
                if not tool_call:
                    visible_answer = _clean_renderer_artifacts(_fallback_after_tool(payload.messages, visible_answer))
                delta = {"content": visible_answer} if not tool_call else {
                    "content": None, "tool_calls": [{"index": 0, **tool_call}]
                }
                yield _sse({"id": request_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": payload.model, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
                yield _sse({
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": payload.model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if tool_call else "stop"}],
                })
                yield _sse("[DONE]")
            except Exception as exc:
                logger.exception("Streaming chat completion failed")
                yield _sse({"error": {"message": redact(str(exc)), "type": "provider_error"}})
                yield _sse("[DONE]")

        return StreamingResponse(events(), media_type="text/event-stream")

    try:
        answer = await provider.complete(prompt, conversation_id=conversation_id)
    except Exception as exc:
        logger.exception("Chat completion failed")
        raise HTTPException(status_code=502, detail=redact(str(exc))) from exc

    tool_call, visible_answer = _extract_tool_call(answer, payload.tools if payload.tool_choice != "none" else None)
    if not tool_call:
        visible_answer = _clean_renderer_artifacts(_fallback_after_tool(payload.messages, visible_answer))
    return _completion_response(request_id, payload.model, visible_answer, [tool_call] if tool_call else None)
