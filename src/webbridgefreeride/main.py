from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import load_config
from .logging import configure_logging
from .providers.router import ProviderRouter
from .security import redact
from .service import DeepSeekService

config = load_config()
configure_logging(config["logging"])
logger = logging.getLogger(__name__)
providers = {"deepseek": DeepSeekService(config)}
router = ProviderRouter(providers, config["providers"]["default"])


class Message(BaseModel):
    role: str
    content: str
    name: str | None = None


class ChatRequest(BaseModel):
    model: str = "deepseek-chat"
    messages: list[Message]
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    user: str | None = None
    conversation_id: str | None = Field(default=None, alias="conversation_id")


def _prompt(messages: list[Message]) -> str:
    return "\n\n".join(
        f"{m.role.upper()}: {m.content}" for m in messages if m.content.strip()
    )


def _completion_response(request_id: str, model: str, answer: str) -> dict[str, Any]:
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
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
    ready_value = all(item.get("ready") for item in status.values())
    return {"status": "ready" if ready_value else "not_ready", "providers": redact(status)}


@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [
            {"id": "deepseek-chat", "object": "model", "owned_by": "deepseek-web"},
            {"id": "deepseek-reasoner", "object": "model", "owned_by": "deepseek-web"},
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completion(payload: ChatRequest):
    prompt = _prompt(payload.messages)
    if not prompt:
        raise HTTPException(status_code=400, detail="messages must contain text")

    request_id = f"chatcmpl-{uuid.uuid4().hex}"
    provider = router.provider_for_model(payload.model)

    if payload.stream:
        async def events():
            try:
                async for chunk in provider.stream_complete(prompt, conversation_id=payload.conversation_id):
                    yield _sse({
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": payload.model,
                        "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
                    })
                yield _sse({
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": payload.model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                })
                yield _sse("[DONE]")
            except Exception as exc:
                logger.exception("Streaming chat completion failed")
                yield _sse({"error": {"message": redact(str(exc)), "type": "provider_error"}})
                yield _sse("[DONE]")

        return StreamingResponse(events(), media_type="text/event-stream")

    try:
        answer = await provider.complete(prompt, conversation_id=payload.conversation_id)
    except Exception as exc:
        logger.exception("Chat completion failed")
        raise HTTPException(status_code=502, detail=redact(str(exc))) from exc

    return _completion_response(request_id, payload.model, answer)
