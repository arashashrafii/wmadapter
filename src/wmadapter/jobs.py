"""Gateway-owned inference jobs.

Provider web requests must not be coupled to the lifetime of one client
connection. A browser tab, reverse proxy, or SDK timeout can interrupt the
HTTP request while the provider is still processing an already-submitted
prompt. This registry lets the request be observed again without resubmitting
it.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


class JobIdempotencyConflict(ValueError):
    """The same idempotency key was reused for a different request."""


@dataclass
class InferenceJob:
    job_id: str
    model: str
    created_at: float
    fingerprint: str
    task: asyncio.Task | None = None
    result: Any = None
    error: BaseException | None = None
    updated_at: float = field(default_factory=time.time)

    @property
    def done(self) -> bool:
        return self.task is not None and self.task.done()

    @property
    def status(self) -> str:
        if self.error is not None:
            return "failed"
        if self.done:
            return "completed"
        return "in_progress"

    async def wait(self) -> Any:
        """Wait without allowing caller cancellation to cancel provider work."""
        if self.task is None:
            raise RuntimeError("inference job has not started")
        await asyncio.shield(self.task)
        if self.error is not None:
            raise self.error
        return self.result


class InferenceJobStore:
    """Process-local job store with bounded retention for completed work."""

    def __init__(self, retention_seconds: float = 3600.0):
        self.retention_seconds = max(0.0, retention_seconds)
        self._jobs: dict[str, InferenceJob] = {}
        self._idempotency: dict[str, str] = {}

    def _purge(self) -> None:
        cutoff = time.time() - self.retention_seconds
        for job_id, job in list(self._jobs.items()):
            if job.done and job.updated_at < cutoff:
                self._jobs.pop(job_id, None)
                for key, mapped_id in list(self._idempotency.items()):
                    if mapped_id == job_id:
                        self._idempotency.pop(key, None)

    def create(
        self,
        job_id: str,
        model: str,
        fingerprint: str,
        factory: Callable[[], Awaitable[Any]],
        *,
        idempotency_key: str | None = None,
    ) -> tuple[InferenceJob, bool]:
        """Create a job, or return the existing job for an idempotency key."""
        self._purge()
        if idempotency_key:
            existing_id = self._idempotency.get(idempotency_key)
            if existing_id:
                existing = self._jobs.get(existing_id)
                if existing is not None:
                    if existing.fingerprint != fingerprint:
                        raise JobIdempotencyConflict(
                            "Idempotency-Key was already used for a different request"
                        )
                    return existing, False
                self._idempotency.pop(idempotency_key, None)

        loop = asyncio.get_running_loop()
        job = InferenceJob(job_id=job_id, model=model, created_at=time.time(), fingerprint=fingerprint)
        job.task = loop.create_task(self._run(job, factory), name=f"wmadapter-inference-{job_id}")
        self._jobs[job_id] = job
        if idempotency_key:
            self._idempotency[idempotency_key] = job_id
        return job, True

    async def _run(self, job: InferenceJob, factory: Callable[[], Awaitable[Any]]) -> None:
        try:
            job.result = await factory()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            job.error = exc
        finally:
            job.updated_at = time.time()

    def get(self, job_id: str) -> InferenceJob | None:
        self._purge()
        return self._jobs.get(job_id)

    def cancel_all(self) -> None:
        for job in self._jobs.values():
            if job.task is not None and not job.task.done():
                job.task.cancel()
        self._jobs.clear()
        self._idempotency.clear()
