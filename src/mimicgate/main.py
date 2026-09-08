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
from .service import DeepSeekService, PageCapacityError, QwenService

from .providers.contract import Message, ChatRequest, ProviderRequest, ProviderResult, canonicalize
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
gateway_api_key = config["server"].get("api_key")


def _authorize(request: Request) -> None:
    """Optional local bearer auth; unset preserves existing loopback behavior."""
    if not gateway_api_key:
        return
    if request.headers.get("authorization") != f"Bearer {gateway_api_key}":
        raise HTTPException(401, "Invalid or missing bearer token", headers={"WWW-Authenticate": "Bearer"})





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


app = FastAPI(title="MimicGate", description="MimicGate — Web-to-API Gateway for AI Agents", version="0.5.0", lifespan=lifespan)


def _error(message, kind="invalid_request_error", code=None):
    return {"error": {"message": message, "type": kind, "param": None, "code": code}}


def _provider_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PageCapacityError):
        return HTTPException(503, "provider_capacity")
    return HTTPException(502, "Web provider failed to produce a valid completion")


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    if exc.status_code == 503:
        messages = {
            "provider_login_required": ("Provider login is required", "provider_login_required"),
            "provider_not_ready": ("Provider is not ready; complete login or challenge verification and retry", "provider_not_ready"),
            "provider_capacity": ("Provider page capacity is temporarily unavailable; close an idle conversation or retry", "provider_capacity"),
        }
        message, code = messages.get(str(exc.detail), (str(exc.detail), "provider_error"))
        return JSONResponse(_error(message, "provider_error", code), status_code=503)
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
    payload = {"status": "ready" if ready_value else "not_ready", "providers": redact(status)}
    if not ready_value:
        return JSONResponse(payload, status_code=503)
    return payload


def _model_catalog():
    return {model: name for name, provider in router.providers.items()
            for model in provider.model_ids}


def _model_provider(model):
    # OpenClaw commonly prefixes the public model with the local gateway name.
    normalized_model = model.split("/", 1)[-1]
    try:
        return router.resolve_model(normalized_model)
    except RuntimeError as exc:
        raise HTTPException(404, "Unknown model") from exc


async def _require_provider_ready(provider) -> None:
    ready = getattr(provider, "ready", None)
    if ready is None:
        status = await provider.status()
        ready = bool(status.get("ready")) if isinstance(status, dict) else False
    if not ready:
        raise HTTPException(503, "provider_not_ready")


@app.get("/props")
async def props(model: str, autoload: bool = False):
    provider = _model_provider(model)
    await _require_provider_ready(provider)
    return {"model": model, "context_length": provider.capabilities.context_window,
            "supports_chat": True, "capabilities": provider.capabilities.model_dump()}


@app.get("/v1/models")
async def models(request: Request):
    _authorize(request)
    return {"object": "list", "data": [
        {"id": model, "object": "model", "created": 0, "owned_by": name + "-web",
         "capabilities": router.providers[name].capabilities.model_dump()}
        for model, name in _model_catalog().items() if name in router.providers
    ]}


@app.post("/v1/chat/completions")
async def chat_completion(payload: ChatRequest, request: Request):
    _authorize(request)
    request_id = f"chatcmpl-{uuid.uuid4().hex}"
    provider = _model_provider(payload.model)
    validate_chat(payload, provider)
    await _require_provider_ready(provider)
    conversation_id = payload.conversation_id or payload.user or _fallback_conversation_id(payload.messages)
    inference = ProviderRequest(
        chat=payload, canonical=canonicalize(payload), conversation_id=conversation_id,
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
            except PageCapacityError:
                yield _sse(_error("Provider page capacity is temporarily unavailable; close an idle conversation or retry", "provider_error", "provider_capacity"))
            except Exception:
                logger.exception("Streaming chat completion failed")
                yield _sse(_error("Web provider failed to produce a valid completion", "provider_error", "provider_error"))
            yield _sse("[DONE]")
        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    try:
        result = await infer()
    except PageCapacityError as exc:
        logger.warning("Chat completion blocked by provider page capacity: %s", exc)
        raise _provider_http_error(exc) from exc
    except Exception as exc:
        logger.exception("Chat completion failed")
        raise _provider_http_error(exc) from exc
    response = _completion_response(request_id, payload.model, result.content or "", result.tool_calls)
    response["choices"][0]["finish_reason"] = result.finish_reason
    response["usage"] = result.usage
    return response
