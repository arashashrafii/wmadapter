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

from .providers.contract import (
    ConversationId,
    Message,
    ChatRequest,
    ProviderRequest,
    ProviderResult,
    ResponseId,
    ResponsesRequest,
    canonicalize,
    normalize_structured_output,
    validate_structured_output,
)
from .providers.state import ConversationStateConflict, GatewayState, UnknownResponseId
from .providers.opencode import translate_request as translate_opencode_request
from .providers.errors import (
    ContextLimitError,
    ProviderInternalError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from .providers.submit import PreSubmitError, UncertainSubmitError
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
response_state = GatewayState()


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
    response_state.clear()


app = FastAPI(title="Web Model Adapter", description="Web Model Adapter — Web-to-API Gateway for AI Agents", version="0.5.0", lifespan=lifespan)


def _error(message, kind="invalid_request_error", code=None):
    return {"error": {"message": message, "type": kind, "param": None, "code": code}}


def _provider_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PageCapacityError):
        return HTTPException(503, "provider_capacity")
    if isinstance(exc, ProviderRateLimitError):
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None
        return HTTPException(429, "provider_rate_limited", headers=headers)
    if isinstance(exc, TimeoutError):
        return HTTPException(504, "provider_timeout")
    if isinstance(exc, ContextLimitError):
        return HTTPException(400, "context_length_exceeded")
    if isinstance(exc, ProviderUnavailableError):
        return HTTPException(503, "provider_unavailable")
    if isinstance(exc, UncertainSubmitError):
        return HTTPException(502, "provider_submission_uncertain")
    if isinstance(exc, PreSubmitError):
        return HTTPException(503, "provider_unavailable")
    message = str(exc).lower()
    if any(marker in message for marker in ("rate limit", "rate_limited", "too many requests", "40029")):
        return HTTPException(429, "provider_rate_limited")
    return HTTPException(502, "provider_internal_error")


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    if exc.status_code == 429:
        message = "Provider rate limit reached"
        return JSONResponse(_error(message, "rate_limit_error", "provider_rate_limited"),
                            status_code=429, headers=exc.headers)
    if exc.status_code == 504:
        return JSONResponse(_error("Provider request timed out", "provider_error", "provider_timeout"),
                            status_code=504)
    if exc.status_code == 503:
        messages = {
            "provider_login_required": ("Provider login is required", "provider_login_required"),
            "provider_not_ready": ("Provider is not ready; complete login or challenge verification and retry", "provider_not_ready"),
            "provider_capacity": ("Provider page capacity is temporarily unavailable; close an idle conversation or retry", "provider_capacity"),
            "provider_unavailable": ("Provider is temporarily unavailable", "provider_unavailable"),
        }
        message, code = messages.get(str(exc.detail), (str(exc.detail), "provider_error"))
        return JSONResponse(_error(message, "provider_error", code), status_code=503)
    kind = "provider_error" if exc.status_code >= 500 else "invalid_request_error"
    code = "model_not_found" if exc.status_code == 404 and "model" in str(exc.detail).lower() else kind
    if exc.status_code == 400 and str(exc.detail) == "context_length_exceeded":
        code = "context_length_exceeded"
    elif exc.status_code == 502 and str(exc.detail) == "provider_submission_uncertain":
        code = "provider_submission_uncertain"
    elif exc.status_code == 502 and str(exc.detail) == "provider_internal_error":
        code = "provider_internal_error"
    if exc.status_code == 400 and "unsupported" in str(exc.detail).lower():
        code = "unsupported_feature"
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


def _public_capabilities(provider):
    capabilities = provider.capabilities.model_dump()
    limits = config.get("limits", {})
    capabilities["gateway_max_input_chars"] = limits.get("max_input_chars")
    capabilities["gateway_max_output_chars"] = limits.get("max_output_chars")
    return capabilities


def _enforce_output_limit(content: str, max_output_chars: int | None) -> None:
    if max_output_chars is not None and len(content) > max_output_chars:
        raise HTTPException(502, "gateway_output_limit")


def _structured_instruction(spec) -> str:
    if spec.type == "json_object":
        return "Return only one valid JSON object. Do not include markdown fences or commentary."
    return (
        "Return only one valid JSON value matching this schema, without markdown fences or commentary: "
        + json.dumps(spec.schema, ensure_ascii=False, separators=(",", ":"))
    )


async def _infer_structured(provider, inference, spec):
    result = await provider.infer(inference)
    try:
        validate_structured_output(result.content or "", spec)
        return result
    except ValueError as first_error:
        original = result.content or ""
        if len(original) > 4096:
            raise HTTPException(502, "structured_output_invalid") from first_error
        repair_prompt = (
            "Repair the following provider output into valid JSON matching the requested format. "
            "Return only the corrected JSON and no explanation.\n\n" + original
        )
        repaired = await provider.complete(repair_prompt, conversation_id=inference.conversation_id)
        try:
            validate_structured_output(repaired, spec)
        except ValueError as second_error:
            raise HTTPException(502, "structured_output_invalid") from second_error
        return ProviderResult(content=repaired)


def _model_provider(model):
    # OpenClaw commonly prefixes the public model with the local gateway name.
    normalized_model = model.split("/", 1)[-1]
    try:
        return router.resolve_model(normalized_model)
    except RuntimeError as exc:
        raise HTTPException(404, "Unknown model") from exc


async def _require_provider_ready(provider) -> None:
    status = await provider.status()
    if isinstance(status, dict) and "ready" in status:
        ready = bool(status["ready"])
    else:
        ready = bool(getattr(provider, "ready", False))
    if not ready:
        raise HTTPException(503, "provider_not_ready")


@app.get("/props")
async def props(model: str, autoload: bool = False):
    provider = _model_provider(model)
    await _require_provider_ready(provider)
    return {"model": model, "context_length": provider.capabilities.context_window,
            "supports_chat": True, "capabilities": _public_capabilities(provider)}


@app.get("/v1/models")
async def models(request: Request):
    _authorize(request)
    return {"object": "list", "data": [
        {"id": model, "object": "model", "created": 0, "owned_by": name + "-web",
         "capabilities": _public_capabilities(router.providers[name])}
        for model, name in _model_catalog().items() if name in router.providers
    ]}


@app.post("/v1/chat/completions")
async def chat_completion(payload: ChatRequest, request: Request):
    _authorize(request)
    request_id = f"chatcmpl-{uuid.uuid4().hex}"
    provider = _model_provider(payload.model)
    validate_chat(payload, provider, config.get("limits", {}).get("max_input_chars"))
    try:
        structured_output = normalize_structured_output(payload.response_format)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if structured_output is not None and payload.tools:
        raise HTTPException(400, "Structured output with tools is not supported")
    await _require_provider_ready(provider)
    conversation_id = payload.conversation_id or payload.user
    if conversation_id is None:
        # OpenClaw may omit both fields. Keep one local provider conversation
        # across stateless turns; explicit identifiers remain isolated.
        conversation_id = "auto:openclaw" if provider.name == "deepseek" else _fallback_conversation_id(payload.messages)
    inference = ProviderRequest(
        chat=payload, canonical=canonicalize(payload), conversation_id=conversation_id,
        structured_output=structured_output,
        system_prompt=("" if provider.name == "qwen" else default_system_prompt)
        + (("\n" + _structured_instruction(structured_output)) if structured_output else ""),
        # The public gateway contract is deliberately independent of the
        # consuming agent (OpenClaw, Hermes, OpenCode, or another client).
    )

    async def infer():
        if _is_title_request(payload.messages):
            return ProviderResult(content=_local_title(payload.messages))
        return await (_infer_structured(provider, inference, structured_output)
                      if structured_output else provider.infer(inference))

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
                _enforce_output_limit(result.content or "", config.get("limits", {}).get("max_output_chars"))
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
                                 headers={"Cache-Control": "no-cache", "Connection": "close", "X-Accel-Buffering": "no"})
    try:
        result = await infer()
    except PageCapacityError as exc:
        logger.warning("Chat completion blocked by provider page capacity: %s", exc)
        raise _provider_http_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Chat completion failed")
        raise _provider_http_error(exc) from exc
    _enforce_output_limit(result.content or "", config.get("limits", {}).get("max_output_chars"))
    response = _completion_response(request_id, payload.model, result.content or "", result.tool_calls)
    response["choices"][0]["finish_reason"] = result.finish_reason
    response["usage"] = result.usage
    return response


@app.post("/v1/opencode/chat/completions")
async def opencode_chat_completion(request: Request):
    """Isolated OpenCode channel; inference remains the shared chat contract."""
    _authorize(request)
    try:
        payload = translate_opencode_request(await request.json())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return await chat_completion(payload, request)


@app.post("/v1/responses")
async def responses(payload: ResponsesRequest, request: Request):
    """Minimal text-only Responses compatibility channel."""
    _authorize(request)
    unsupported = sorted(payload.model_extra or {})
    if unsupported:
        raise HTTPException(400, f"Responses feature is not supported: {unsupported[0]}")
    if payload.stream:
        raise HTTPException(400, "Responses streaming is not supported yet")
    if not isinstance(payload.input, str):
        raise HTTPException(400, "Multimodal or structured Responses input is not supported yet")

    provider = _model_provider(payload.model)
    text_format = payload.text.get("format") if payload.text is not None else None
    if payload.text is not None and text_format is None:
        raise HTTPException(400, "Responses text.format is required")
    try:
        structured_output = normalize_structured_output(text_format=text_format)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    chat = ChatRequest(
        model=payload.model,
        messages=[Message(role="user", content=payload.input)],
        conversation_id=payload.conversation_id,
        previous_response_id=payload.previous_response_id,
    )
    validate_chat(chat, provider, config.get("limits", {}).get("max_input_chars"))
    await _require_provider_ready(provider)
    try:
        conversation_id = response_state.resolve(
            conversation_id=payload.conversation_id,
            previous_response_id=payload.previous_response_id,
        )
    except UnknownResponseId as exc:
        raise HTTPException(400, "Unknown or expired previous_response_id") from exc
    except ConversationStateConflict as exc:
        raise HTTPException(400, str(exc)) from exc
    if conversation_id is None:
        conversation_id = ConversationId(f"resp-{uuid.uuid4().hex}")

    inference = ProviderRequest(
        chat=chat,
        canonical=canonicalize(chat),
        conversation_id=conversation_id,
        previous_response_id=payload.previous_response_id,
        structured_output=structured_output,
        system_prompt=("" if provider.name == "qwen" else default_system_prompt)
        + (("\n" + _structured_instruction(structured_output)) if structured_output else ""),
    )
    try:
        result = await (_infer_structured(provider, inference, structured_output)
                        if structured_output else provider.infer(inference))
    except PageCapacityError as exc:
        raise _provider_http_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _provider_http_error(exc) from exc

    _enforce_output_limit(result.content or "", config.get("limits", {}).get("max_output_chars"))
    response_id = ResponseId(f"resp_{uuid.uuid4().hex}")
    response_state.remember(response_id, conversation_id)
    text = result.content or ""
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "model": payload.model,
        "conversation_id": conversation_id,
        "output": [{
            "id": f"msg_{uuid.uuid4().hex}",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }],
        "output_text": text,
    }
