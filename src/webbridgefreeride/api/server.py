from fastapi import FastAPI

app = FastAPI(title="WebBridgeFreeRide")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/v1/chat/completions")
def chat_completion(request: dict):
    return {
        "id": "webbridge-test",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "DeepSeek adapter is not connected yet."
                }
            }
        ]
    }
