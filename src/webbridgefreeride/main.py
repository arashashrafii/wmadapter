from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from .api.validation import validate_chat

from .config import load_config
from .logging import configure_logging
from .providers.router import ProviderRouter
from .security import redact
from .service import DeepSeekService, QwenService

from .providers.contract import Message, ChatRequest, ProviderRequest, ProviderResult
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
    _extract_tool_call,
)

config = load_config()
configure_logging(config["logging"])
logger = logging.getLogger(__name__)
providers = {"deepseek": DeepSeekService(config), "qwen": QwenService(config)}
router = ProviderRouter(providers, config["providers"]["default"])
default_system_prompt = config["deepseek"].get("system_prompt", "")





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
        "usage": None,
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


def _error(message, kind="invalid_request_error", code=None):
    return {"error": {"message": message, "type": kind, "param": None, "code": code}}


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    kind = "provider_error" if exc.status_code >= 500 else "invalid_request_error"
    code = "model_not_found" if exc.status_code == 404 and "model" in str(exc.detail).lower() else kind
    return JSONResponse(_error(str(exc.detail), kind, code), status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(_error("Invalid request body", code="invalid_request"), status_code=400)



@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    status = await router.status()
    enabled = config["providers"].get("enabled", [config["providers"]["default"]])
    ready_value = all(status.get(name, {}).get("ready") for name in enabled)
    return {"status": "ready" if ready_value else "not_ready", "providers": redact(status)}


def _model_catalog():
    return {model: name for name, provider in router.providers.items()
            for model in provider.model_ids}


def _model_provider(model):
    # Preserve provider-prefixed legacy aliases only for known models.
    plain = model.split(":", 1)[-1]
    name = _model_catalog().get(plain)
    if name is None or (":" in model and model.split(":", 1)[0] != name):
        raise HTTPException(404, "Unknown model")
    provider = router.providers.get(name)
    if provider is None:
        raise HTTPException(404, "Model provider is not configured")
    return provider


@app.get("/props")
async def props(model: str, autoload: bool = False):
    provider = _model_provider(model)
    return {"model": model, "context_length": provider.capabilities.context_window,
            "supports_chat": True, "capabilities": provider.capabilities.model_dump()}


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [
        {"id": model, "object": "model", "created": 0, "owned_by": name + "-web",
         "capabilities": router.providers[name].capabilities.model_dump()}
        for model, name in _model_catalog().items() if name in router.providers
    ]}


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
    provider = _model_provider(payload.model)
    validate_chat(payload, provider)
    conversation_id = (
        request.headers.get("x-openclaw-session-key")
        or request.headers.get("x-openclaw-session-id")
        or payload.conversation_id or payload.user
        or _fallback_conversation_id(payload.messages)
    )
    inference = ProviderRequest(
        chat=payload, conversation_id=conversation_id,
        system_prompt="" if provider.name == "qwen" else default_system_prompt,
        # The public gateway contract is deliberately independent of the
        # consuming agent (OpenClaw, Hermes, OpenCode, or another client).
    )

    async def infer():
        if _is_title_request(payload.messages):
            return ProviderResult(content=_local_title(payload.messages))
        return await provider.infer(inference)

    if payload.stream:
        async def events():
            def chunk(delta, finish=None):
                return {"id": request_id, "object": "chat.completion.chunk",
                        "created": created, "model": payload.model,
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            created = int(time.time())
            yield _sse(chunk({"role": "assistant", "content": ""}))
            try:
                result = await infer()
                delta = {"content": result.content or ""}
                if result.tool_calls:
                    delta = {"tool_calls": [{"index": i, **call} for i, call in enumerate(result.tool_calls)]}
                yield _sse(chunk(delta))
                yield _sse(chunk({}, result.finish_reason))
                if (getattr(payload, "stream_options", None) or {}).get("include_usage"):
                    yield _sse({"id": request_id, "object": "chat.completion.chunk",
                                "created": created, "model": payload.model,
                                "choices": [], "usage": result.usage})
            except Exception:
                logger.exception("Streaming chat completion failed")
                yield _sse(_error("Web provider failed to produce a valid completion", "provider_error", "provider_error"))
            yield _sse("[DONE]")
        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    try:
        result = await infer()
    except Exception as exc:
        logger.exception("Chat completion failed")
        raise HTTPException(502, "Web provider failed to produce a valid completion") from exc
    response = _completion_response(request_id, payload.model, result.content or "", result.tool_calls)
    response["choices"][0]["finish_reason"] = result.finish_reason
    response["usage"] = result.usage
    return response
