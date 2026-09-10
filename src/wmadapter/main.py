from __future__ import annotations

import json
import logging
import time
import uuid
import asyncio
from contextlib import suppress
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket
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
    EmbeddingsRequest,
    AudioInputRequest,
    AudioSpeechRequest,
    ImagesRequest,
    RealtimeRequest,
    FilesRequest,
    BatchCreateRequest,
    LegacyCompletionRequest,
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

STREAM_HEARTBEAT_SECONDS = 5.0
STREAM_WATCHDOG_SECONDS = 90.0
providers = {"deepseek": DeepSeekService(config), "qwen": QwenService(config)}
router = ProviderRouter(providers, config["providers"]["default"], config["providers"].get("enabled"))
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


def _legacy_completion_request(payload: LegacyCompletionRequest) -> ChatRequest:
    unsupported = sorted(payload.model_extra or {})
    if unsupported:
        raise HTTPException(400, f"Unsupported legacy completions field: {unsupported[0]}")
    if not isinstance(payload.prompt, str):
        raise HTTPException(400, "Legacy completions prompt must be a string")
    return ChatRequest(
        model=payload.model,
        messages=[Message(role="user", content=payload.prompt)],
        stream=payload.stream,
        user=payload.user,
    )


def _legacy_stream_event(data: str) -> str:
    if data == "[DONE]":
        return "data: [DONE]\n\n"
    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        return f"data: {data}\n\n"
    choices = event.get("choices") or []
    if choices:
        choice = choices[0]
        delta = choice.get("delta") or {}
        event["object"] = "text_completion"
        event["choices"] = [{
            "text": delta.get("content") or "",
            "index": choice.get("index", 0),
            "logprobs": None,
            "finish_reason": choice.get("finish_reason"),
        }]
    else:
        event["object"] = "text_completion"
    return "data: " + json.dumps(event, separators=(",", ":")) + "\n\n"


def _sse(data: dict[str, Any] | str) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, separators=(",", ":"))
    return f"data: {payload}\n\n"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await router.start()
    except Exception as exc:
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
    if exc.status_code == 501:
        code = str(exc.detail)
        messages = {
            "embeddings_not_supported": "Embeddings are not supported by the configured web providers",
            "image_generation_not_supported": "Image generation is not supported by the configured web providers",
            "audio_not_supported": "Audio input and output are not supported by the configured web providers",
            "realtime_not_supported": "Realtime sessions are not supported by the configured web providers",
            "files_not_supported": "File and PDF inputs are not supported by the configured web providers",
            "batches_not_supported": "Batch processing is not supported by the configured web providers",
        }
        return JSONResponse(_error(messages.get(code, "Requested capability is not supported by the configured web providers"),
                                   "invalid_request_error", code), status_code=501)
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


def _observed_usage(result: ProviderResult) -> dict[str, int] | None:
    """Expose usage only when the provider supplied a complete safe record."""
    usage = getattr(result, "usage", None)
    required = ("prompt_tokens", "completion_tokens", "total_tokens")
    if not isinstance(usage, dict) or any(key not in usage for key in required):
        return None
    values = {key: usage[key] for key in required}
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
           for value in values.values()):
        return None
    if values["total_tokens"] != values["prompt_tokens"] + values["completion_tokens"]:
        return None
    return values


def _model_limits(provider) -> dict[str, int | None]:
    capabilities = provider.capabilities
    limits = config.get("limits", {})
    return {
        "context_window": capabilities.context_window,
        "max_output_tokens": capabilities.max_output_tokens,
        "gateway_max_input_chars": limits.get("max_input_chars"),
        "gateway_max_output_chars": limits.get("max_output_chars"),
    }


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
    return {"model": model, "provider": provider.name,
            "context_length": provider.capabilities.context_window,
            "supports_chat": True, "capabilities": _public_capabilities(provider),
            "limits": _model_limits(provider)}


@app.get("/v1/models")
async def models(request: Request):
    _authorize(request)
    return {"object": "list", "data": [
        {"id": model, "object": "model", "created": 0, "owned_by": name + "-web",
         "provider": name, "capabilities": _public_capabilities(router.providers[name]),
         "limits": _model_limits(router.providers[name])}
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
            inference_task = asyncio.create_task(infer())
            try:
                deadline = asyncio.get_running_loop().time() + STREAM_WATCHDOG_SECONDS
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    try:
                        result = await asyncio.wait_for(
                            asyncio.shield(inference_task),
                            min(STREAM_HEARTBEAT_SECONDS, remaining),
                        )
                        break
                    except asyncio.TimeoutError:
                        if inference_task.done():
                            result = inference_task.result()
                            break
                        yield ": keep-alive\n\n"
                _enforce_output_limit(result.content or "", config.get("limits", {}).get("max_output_chars"))
                delta = {"content": result.content or ""}
                if result.tool_calls:
                    delta = {"tool_calls": [{"index": i, **call} for i, call in enumerate(result.tool_calls)]}
                yield _sse(chunk(delta))
                yield _sse(chunk({}, result.finish_reason))
                if (getattr(payload, "stream_options", None) or {}).get("include_usage"):
                    yield _sse({"id": request_id, "object": "chat.completion.chunk",
                                "created": created, "model": payload.model,
                                "choices": [], "usage": _observed_usage(result)})
            except (asyncio.TimeoutError, TimeoutError):
                inference_task.cancel()
                with suppress(asyncio.CancelledError, asyncio.TimeoutError):
                    await asyncio.wait_for(inference_task, 0.1)
                yield _sse(_error("Provider request timed out", "provider_error", "provider_timeout"))
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
    response["usage"] = _observed_usage(result)
    return response


@app.post("/v1/completions")
async def legacy_completions(request: Request):
    """Compatibility subset for clients using the legacy text API."""
    _authorize(request)
    try:
        payload = LegacyCompletionRequest.model_validate(await request.json())
        chat = _legacy_completion_request(payload)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, "Invalid legacy completions request") from exc

    result = await chat_completion(chat, request)
    if not chat.stream:
        result["id"] = result["id"].replace("chatcmpl-", "cmpl-", 1)
        result["object"] = "text_completion"
        choice = result["choices"][0]
        result["choices"] = [{
            "text": (choice.get("message") or {}).get("content") or "",
            "index": choice.get("index", 0),
            "logprobs": None,
            "finish_reason": choice.get("finish_reason"),
        }]
        return result

    async def events():
        async for chunk in result.body_iterator:
            text = chunk.decode() if isinstance(chunk, bytes) else chunk
            for line in text.splitlines():
                if line.startswith("data: "):
                    yield _legacy_stream_event(line[6:])
                elif line.startswith(":"):
                    yield line + "\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "close"})


@app.post("/v1/embeddings")
async def embeddings(payload: EmbeddingsRequest, request: Request):
    """Validate the embeddings contract without fabricating vectors."""
    _authorize(request)
    unsupported = sorted(payload.model_extra or {})
    if unsupported:
        raise HTTPException(400, f"Unsupported embeddings field: {unsupported[0]}")
    if isinstance(payload.input, str):
        inputs = [payload.input]
    elif isinstance(payload.input, list) and all(isinstance(item, str) for item in payload.input):
        inputs = payload.input
    else:
        raise HTTPException(400, "Embeddings input must be a string or list of strings")
    if not inputs or any(not item for item in inputs):
        raise HTTPException(400, "Embeddings input must not be empty")
    provider = _model_provider(payload.model)
    if not provider.capabilities.embeddings:
        raise HTTPException(501, "embeddings_not_supported")
    raise HTTPException(501, "embeddings_not_supported")


@app.post("/v1/images")
async def images(payload: ImagesRequest, request: Request):
    """Validate the image-generation contract without fabricating images."""
    _authorize(request)
    unsupported = sorted(payload.model_extra or {})
    if unsupported:
        raise HTTPException(400, f"Unsupported image generation field: {unsupported[0]}")
    if not isinstance(payload.prompt, str) or not payload.prompt.strip():
        raise HTTPException(400, "Image generation prompt must be a non-empty string")
    _model_provider(payload.model)
    raise HTTPException(501, "image_generation_not_supported")


def _unsupported_request_fields(payload: Any, label: str) -> None:
    unsupported = sorted(payload.model_extra or {})
    if unsupported:
        raise HTTPException(400, f"Unsupported {label} field: {unsupported[0]}")


def _require_nonempty_text(value: Any, message: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(400, message)


def _reject_responses_sampling_controls(payload: ResponsesRequest) -> None:
    if payload.max_tokens is not None and payload.max_completion_tokens is not None:
        raise HTTPException(400, "Specify only one of max_tokens or max_completion_tokens")
    for field in (
        "temperature", "top_p", "max_tokens", "max_completion_tokens",
        "max_output_tokens", "presence_penalty", "frequency_penalty", "seed", "stop",
    ):
        if getattr(payload, field, None) is not None:
            raise HTTPException(400, f"Unsupported Responses sampling control: {field}")


def _reject_responses_tools(payload: ResponsesRequest) -> None:
    if payload.tools is not None:
        raise HTTPException(400, "Responses tools are not supported by the web adapter")
    if payload.tool_choice is not None:
        raise HTTPException(400, "Responses tool_choice is not supported by the web adapter")
    if payload.parallel_tool_calls is not None:
        raise HTTPException(400, "Responses parallel_tool_calls is not supported by the web adapter")


@app.post("/v1/audio/speech")
async def audio_speech(payload: AudioSpeechRequest, request: Request):
    """Validate speech synthesis input without fabricating audio."""
    _authorize(request)
    _unsupported_request_fields(payload, "audio speech")
    _require_nonempty_text(payload.input, "Audio speech input must be a non-empty string")
    _require_nonempty_text(payload.voice, "Audio speech voice must be a non-empty string")
    raise HTTPException(501, "audio_not_supported")


@app.post("/v1/audio/transcriptions")
async def audio_transcriptions(payload: AudioInputRequest, request: Request):
    """Validate transcription input without reading or retaining audio."""
    _authorize(request)
    _unsupported_request_fields(payload, "audio transcription")
    _require_nonempty_text(payload.input, "Audio transcription input must be a non-empty string")
    raise HTTPException(501, "audio_not_supported")


@app.post("/v1/audio/translations")
async def audio_translations(payload: AudioInputRequest, request: Request):
    """Validate translation input without reading or retaining audio."""
    _authorize(request)
    _unsupported_request_fields(payload, "audio translation")
    _require_nonempty_text(payload.input, "Audio translation input must be a non-empty string")
    raise HTTPException(501, "audio_not_supported")


@app.post("/v1/realtime")
async def realtime(payload: RealtimeRequest, request: Request):
    """Validate a realtime session request without opening a fake session."""
    _authorize(request)
    _unsupported_request_fields(payload, "realtime")
    raise HTTPException(501, "realtime_not_supported")


@app.post("/v1/files")
async def files(payload: FilesRequest, request: Request):
    """Validate file metadata without accepting, storing, or parsing files."""
    _authorize(request)
    _unsupported_request_fields(payload, "files")
    _require_nonempty_text(payload.purpose, "File purpose must be a non-empty string")
    if payload.file is None:
        raise HTTPException(400, "File content is required")
    raise HTTPException(501, "files_not_supported")


@app.get("/v1/files/{file_id}")
async def file_metadata(file_id: str, request: Request):
    """Reject file metadata lookup because no file store exists."""
    _authorize(request)
    if not file_id:
        raise HTTPException(400, "File ID is required")
    raise HTTPException(501, "files_not_supported")


@app.delete("/v1/files/{file_id}")
async def delete_file(file_id: str, request: Request):
    """Reject file deletion because no file store exists."""
    _authorize(request)
    if not file_id:
        raise HTTPException(400, "File ID is required")
    raise HTTPException(501, "files_not_supported")


@app.post("/v1/batches")
async def create_batch(payload: BatchCreateRequest, request: Request):
    """Validate batch creation without creating an asynchronous job."""
    _authorize(request)
    _unsupported_request_fields(payload, "batches")
    _require_nonempty_text(payload.input_file_id, "Batch input_file_id must be a non-empty string")
    if payload.endpoint not in {"/v1/chat/completions", "/v1/completions", "/v1/embeddings"}:
        raise HTTPException(400, "Unsupported batch endpoint")
    if payload.completion_window != "24h":
        raise HTTPException(400, "Unsupported batch completion_window")
    raise HTTPException(501, "batches_not_supported")


@app.get("/v1/batches")
async def list_batches(request: Request):
    """Reject batch listing because no asynchronous job store exists."""
    _authorize(request)
    raise HTTPException(501, "batches_not_supported")


@app.get("/v1/batches/{batch_id}")
async def retrieve_batch(batch_id: str, request: Request):
    """Reject batch retrieval because no asynchronous job store exists."""
    _authorize(request)
    if not batch_id:
        raise HTTPException(400, "Batch ID is required")
    raise HTTPException(501, "batches_not_supported")


@app.post("/v1/batches/{batch_id}/cancel")
async def cancel_batch(batch_id: str, request: Request):
    """Reject batch cancellation because no asynchronous job store exists."""
    _authorize(request)
    if not batch_id:
        raise HTTPException(400, "Batch ID is required")
    raise HTTPException(501, "batches_not_supported")


@app.websocket("/v1/realtime")
async def realtime_websocket(websocket: WebSocket):
    """Reject websocket realtime sessions until a provider path is verified."""
    await websocket.close(code=1008, reason="realtime_not_supported")


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
    _reject_responses_sampling_controls(payload)
    _reject_responses_tools(payload)
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
        "usage": _observed_usage(result),
    }
