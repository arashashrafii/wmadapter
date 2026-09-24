import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from wmadapter import main
from wmadapter.providers.base import ChatProvider
from wmadapter.providers.contract import ProviderResult
from wmadapter.providers.router import ProviderRouter


class SlowProvider(ChatProvider):
    name = "deepseek"
    model_ids = ("deepseek-chat",)

    async def start(self):
        pass

    async def stop(self):
        pass

    async def status(self):
        return {"ready": True}

    async def complete(self, prompt, conversation_id=None):
        return "unused"


class LongRunningRequestTests(unittest.TestCase):
    def setUp(self):
        self.provider = SlowProvider()
        self.provider.infer = AsyncMock()
        self.patch = patch.object(main, "router", ProviderRouter({"deepseek": self.provider}, "deepseek"))
        self.patch.start()
        main.inference_jobs.cancel_all()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.addCleanup(main.inference_jobs.cancel_all)
        self.addCleanup(self.patch.stop)

    def payload(self):
        return {"model": "deepseek-chat", "messages": [{"role": "user", "content": "build a site"}]}

    def test_async_request_can_be_polled_and_retried_without_duplicate_provider_call(self):
        async def delayed(_request):
            await asyncio.sleep(0.05)
            return ProviderResult(content="site complete")

        self.provider.infer.side_effect = delayed
        headers = {"X-WMAdapter-Async": "true", "Idempotency-Key": "site-42"}
        first = self.client.post("/v1/chat/completions", json=self.payload(), headers=headers)
        self.assertIn(first.status_code, (200, 202))

        if first.status_code == 202:
            body = first.json()
            self.assertEqual(body["status"], "in_progress")
            for _ in range(20):
                response = self.client.get(body["poll_url"])
                if response.status_code == 200:
                    break
                self.assertEqual(response.status_code, 202)
                asyncio.run(asyncio.sleep(0.01))
            else:
                self.fail("long-running request did not complete")
        else:
            response = first

        self.assertEqual(response.json()["choices"][0]["message"]["content"], "site complete")
        retry = self.client.post("/v1/chat/completions", json=self.payload(), headers={"Idempotency-Key": "site-42"})
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["id"], response.json()["id"])
        self.assertEqual(self.provider.infer.await_count, 1)


if __name__ == "__main__":
    unittest.main()
