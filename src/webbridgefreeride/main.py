from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .config import load_config
from .service import DeepSeekService

config = load_config()
service = DeepSeekService(config)


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str = "deepseek-chat"
    messages: list[Message]
    stream: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await service.start()
    except Exception:
        # Keep health endpoint available so startup problems can be diagnosed.
        pass
    yield
    await service.stop()


app = FastAPI(title="WebBridgeFreeRide", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


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
    if payload.stream:
        raise HTTPException(status_code=501, detail="Streaming is not implemented in Milestone 1")

    prompt = "\n\n".join(
        f"{m.role.upper()}: {m.content}" for m in payload.messages if m.content.strip()
    )
    if not prompt:
        raise HTTPException(status_code=400, detail="messages must contain text")

    try:
        answer = await service.complete(prompt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": payload.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
