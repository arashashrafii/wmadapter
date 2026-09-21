"""Provider-neutral retry classification and bounded recovery timing.

The browser adapters own submission-state inspection and reconciliation.  This
module only decides whether a failure is safe to retry and bounds the repair
window; ambiguous submissions remain fail-closed unless explicitly enabled for
a canonical request proven to have no tools or side effects.
"""
from __future__ import annotations

import asyncio
import random
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Awaitable, Callable

from .errors import ProviderInternalError, ProviderRateLimitError, ProviderUnavailableError
from .submit import PreSubmitError, UncertainSubmitError


class FailureClass(StrEnum):
    PRE_SUBMIT = "pre_submit"
    SUBMITTED_UNOBSERVED = "submitted_unobserved"
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    UNAVAILABLE = "unavailable"
    PROTOCOL = "protocol"


@dataclass(frozen=True)
class RequestRecoveryContext:
    """Safety facts supplied by the canonical request boundary."""

    # Direct provider calls have unknown side effects; only the canonical
    # request path can prove that no tools were requested.
    has_tools_or_side_effects: bool = True


_request_context: ContextVar[RequestRecoveryContext] = ContextVar(
    "wmadapter_request_recovery_context", default=RequestRecoveryContext()
)


@contextmanager
def recovery_context(*, has_tools_or_side_effects: bool):
    token = _request_context.set(RequestRecoveryContext(has_tools_or_side_effects))
    try:
        yield
    finally:
        _request_context.reset(token)


def current_recovery_context() -> RequestRecoveryContext:
    return _request_context.get()


@dataclass(frozen=True)
class RecoveryPolicy:
    """Conservative recovery settings shared by web provider services."""

    enabled: bool = True
    max_attempts: int = 2
    backoff_base_ms: int = 250
    backoff_max_ms: int = 5000
    deadline_ms: int = 120000
    allow_resend: bool = False

    def deadline(self, now: float | None = None) -> float:
        return (time.monotonic() if now is None else now) + self.deadline_ms / 1000

    def delay_ms(self, retry_number: int) -> int:
        """Return jittered exponential delay for retry number 1..N."""
        ceiling = min(self.backoff_max_ms, self.backoff_base_ms * (2 ** max(0, retry_number - 1)))
        return int(random.uniform(0, max(0, ceiling))) if ceiling else 0

    def can_retry(
        self,
        failure: FailureClass,
        *,
        submitted: bool = False,
        has_tools_or_side_effects: bool = True,
    ) -> bool:
        """Return whether another submission is safe under this policy.

        A pre-submit failure is safe even for a tool-bearing request because
        the irreversible browser gesture has not happened.  A submitted
        A submitted request can only be replayed when the caller explicitly
        enables it and the canonical request has no tools or side effects.
        The default remains fail-closed.
        """
        if not self.enabled:
            return False
        if failure == FailureClass.PRE_SUBMIT and not submitted:
            return True
        if failure == FailureClass.SUBMITTED_UNOBSERVED:
            return self.allow_resend and not has_tools_or_side_effects
        return failure == FailureClass.UNAVAILABLE and not submitted

    async def wait_before_retry(self, retry_number: int, deadline: float) -> bool:
        if not self.enabled:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        delay = min(self.delay_ms(retry_number) / 1000, remaining)
        if delay:
            await asyncio.sleep(delay)
        return time.monotonic() < deadline


def classify_failure(exc: Exception, *, submitted: bool = False) -> FailureClass:
    """Classify a provider failure without retaining its message."""
    if isinstance(exc, UncertainSubmitError) or submitted:
        return FailureClass.SUBMITTED_UNOBSERVED
    if isinstance(exc, ProviderRateLimitError) or _has_marker(exc, "rate_limited", "rate limit", "too many requests", "40029"):
        return FailureClass.RATE_LIMIT
    if _has_marker(exc, "provider_login_required", "challenge_visible", "sign_in_visible", "unknown_ui", "session_pending", "account_suspended", "login_required"):
        return FailureClass.AUTHENTICATION
    if isinstance(exc, PreSubmitError):
        return FailureClass.PRE_SUBMIT
    if isinstance(exc, ProviderUnavailableError):
        return FailureClass.UNAVAILABLE
    if isinstance(exc, (ProviderInternalError, ValueError, TypeError)):
        return FailureClass.PROTOCOL
    return FailureClass.UNAVAILABLE


def safe_failure(exc: Exception) -> str:
    """Return a stable diagnostic that cannot contain provider text."""
    return f"provider_request_failed:{type(exc).__name__}"


def _has_marker(exc: Exception, *markers: str) -> bool:
    message = str(exc).casefold()
    return any(marker.casefold() in message for marker in markers)


async def bounded_observe(
    observe: Callable[[int], Awaitable[str | None]],
    *,
    timeout_ms: int,
    deadline: float,
) -> str | None:
    """Give a provider adapter only the time left in the recovery budget."""
    remaining_ms = min(timeout_ms, max(0, int((deadline - time.monotonic()) * 1000)))
    if remaining_ms <= 0:
        return None
    return await observe(remaining_ms)
