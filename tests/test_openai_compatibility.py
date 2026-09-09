import json
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from wmadapter import main
from wmadapter.providers.base import ChatProvider
from wmadapter.providers.router import ProviderRouter


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "openai_compatibility.json"
TOOLS = [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]


class CompatibilityFixtureProvider(ChatProvider):
    name = "deepseek"
    model_ids = ("deepseek-chat",)

    async def start(self):
        pass

    async def stop(self):
        pass

    async def status(self):
        return {"ready": True}

    async def complete(self, prompt, conversation_id=None):
        if "TOOL_RESULT" in prompt:
            return "42"
        if "Available tools:" in prompt:
            return '<tool_call>{"name":"lookup","arguments":{}}</tool_call>'
        return "hello"


class OpenAICompatibilityL1Tests(unittest.TestCase):
    def setUp(self):
        provider = CompatibilityFixtureProvider()
        provider.complete = AsyncMock(side_effect=provider.complete)
        self.patch = patch.object(main, "router", ProviderRouter({"deepseek": provider}, "deepseek"))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.fixture = json.loads(FIXTURE_PATH.read_text())

    def post(self, **overrides):
        request = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": "hello"}],
        }
        request.update(overrides)
        return self.client.post("/v1/chat/completions", json=request)

    def test_deterministic_text_and_tool_roundtrip(self):
        text = self.post()
        self.assertEqual(text.status_code, 200)
        self.assertEqual(text.json()["choices"][0]["message"]["content"], self.fixture["cases"]["text"]["response"]["content"])

        first = self.post(messages=[{"role": "user", "content": "look this up"}], tools=TOOLS)
        self.assertEqual(first.json()["choices"][0]["finish_reason"], "tool_calls")
        call = first.json()["choices"][0]["message"]["tool_calls"][0]
        second = self.post(messages=[
            {"role": "user", "content": "look this up"},
            first.json()["choices"][0]["message"],
            {"role": "tool", "tool_call_id": call["id"], "content": "42"},
        ], tools=TOOLS)
        self.assertEqual(second.json()["choices"][0]["message"]["content"], "42")

    def test_deterministic_sse_is_openai_shaped(self):
        response = self.post(stream=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith(self.fixture["cases"]["sse"]["response"]["content_type"]))
        frames = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
        self.assertEqual(frames[-1], self.fixture["cases"]["sse"]["response"]["terminal_frame"])
        chunks = [json.loads(frame) for frame in frames[:-1]]
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], self.fixture["cases"]["sse"]["response"]["finish_reason"])


class OpenAICompatibilityL2Tests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("WMADAPTER_OPENAI_COMPAT_L2") == "1",
        "BLOCKED: set WMADAPTER_OPENAI_COMPAT_L2=1 with a running local fixture endpoint",
    )
    def test_official_openai_python_sdk_black_box(self):
        try:
            from openai import OpenAI
        except ImportError as error:
            self.skipTest(f"SKIPPED: official OpenAI SDK unavailable ({error})")

        client = OpenAI(
            api_key="fixture",
            base_url=os.environ.get("WMADAPTER_TEST_URL", "http://127.0.0.1:18761/v1"),
        )
        response = client.chat.completions.create(
            model="deepseek-chat", messages=[{"role": "user", "content": "hello"}]
        )
        self.assertEqual(response.choices[0].message.content, "hello")
