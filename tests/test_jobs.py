import asyncio
import unittest

from wmadapter.jobs import InferenceJobStore, JobIdempotencyConflict


class InferenceJobStoreTests(unittest.TestCase):
    def test_caller_cancellation_does_not_cancel_provider_job(self):
        async def scenario():
            store = InferenceJobStore()
            started = asyncio.Event()
            release = asyncio.Event()

            async def work():
                started.set()
                await release.wait()
                return "finished"

            job, created = store.create("job-1", "model", "fingerprint", work)
            self.assertTrue(created)
            await started.wait()

            waiter = asyncio.create_task(job.wait())
            await asyncio.sleep(0)
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter

            release.set()
            self.assertEqual(await job.wait(), "finished")
            self.assertEqual(job.status, "completed")
            store.cancel_all()

        asyncio.run(scenario())

    def test_idempotency_reuses_running_job_and_rejects_payload_change(self):
        async def work():
            await asyncio.sleep(0)
            return "finished"

        async def scenario():
            store = InferenceJobStore()
            first, created = store.create(
                "job-1", "model", "same", work, idempotency_key="request-1"
            )
            second, reused = store.create(
                "job-2", "model", "same", work, idempotency_key="request-1"
            )
            self.assertTrue(created)
            self.assertFalse(reused)
            self.assertIs(first, second)
            with self.assertRaises(JobIdempotencyConflict):
                store.create("job-3", "model", "different", work, idempotency_key="request-1")
            await first.wait()
            store.cancel_all()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
