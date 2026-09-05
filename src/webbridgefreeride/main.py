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


from .providers.contract import Message, ChatRequest
from .providers.protocol import (
    _content_text,
    _image_attachments,
    _prompt,
    _is_title_request,
    _local_title,
    _fallback_conversation_id,
    _fallback_after_tool,
    _resolve_web_answer,
    _clean_renderer_artifacts,
    _normalize_tool_arguments,
    _extract_tool_call ,
)


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


@app.get("/v1/conversations/{conversation_id}")
async def conversation_details(conversation_id: str, model: str = "deepseek-chat"):
    """Expose only the active Web conversation URL to the trusted cleanup plugin."""
    provider = router.provider_for_model(model)
    url = provider.conversation_url(conversation_id) if hasattr(provider, "conversation_url") else None
    return {"object": "conversation", "id": conversation_id, "url": url}


@app.post("/v1/conversations/bind")
async def bind_conversation(payload: dict[str, str], model: str = "deepseek-chat"):
    """Bind OpenClaw session identifiers before the first WebBridge request."""
    session_id = payload.get("session_id", "")
    session_key = payload.get("session_key", "")
    if not session_id or not session_key:
        raise HTTPException(status_code=400, detail="session_id and session_key are required")
    provider = router.provider_for_model(model)
    if not hasattr(provider, "bind_conversation"):
        raise HTTPException(status_code=400, detail="provider does not support session binding")
    provider.bind_conversation(session_id, session_key)
    return {"object": "conversation_binding", "session_id": session_id, "bound": True}


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
    image_attachments = _image_attachments(payload.messages)
    if image_attachments and not prompt:
        prompt = "USER: Please analyze the attached image."
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
    logger.info("Resolved conversation mapping id=%s", conversation_id)

    if payload.stream:
        async def events():
            try:
                answer = ""
                if image_attachments and hasattr(provider, "stream_complete_with_attachments"):
                    chunks = provider.stream_complete_with_attachments(
                        prompt, conversation_id=conversation_id, attachments=image_attachments
                    )
                else:
                    chunks = provider.stream_complete(prompt, conversation_id=conversation_id)
                async for chunk in chunks:
                    answer += chunk
                tool_call, visible_answer = await _resolve_web_answer(
                    provider, answer, payload.messages,
                    payload.tools if payload.tool_choice != "none" else None, conversation_id, prompt,
                )
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
        if image_attachments and hasattr(provider, "complete_with_attachments"):
            answer = await provider.complete_with_attachments(
                prompt, conversation_id=conversation_id, attachments=image_attachments
            )
        else:
            answer = await provider.complete(prompt, conversation_id=conversation_id)
        tool_call, visible_answer = await _resolve_web_answer(
            provider, answer, payload.messages,
            payload.tools if payload.tool_choice != "none" else None, conversation_id, prompt,
        )
    except Exception as exc:
        logger.exception("Chat completion failed")
        raise HTTPException(status_code=502, detail=redact(str(exc))) from exc

    return _completion_response(request_id, payload.model, visible_answer, [tool_call] if tool_call else None)
