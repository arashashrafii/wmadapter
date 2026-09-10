"""Typed provider failures used at the HTTP compatibility boundary."""
from __future__ import annotations


class ProviderUnavailableError(RuntimeError):
    """The upstream provider is unavailable without a known retry delay."""


class ProviderTimeoutError(TimeoutError):
    """The provider did not complete within its configured timeout."""


class ProviderRateLimitError(RuntimeError):
    """The provider reported rate limiting, optionally with a known delay."""

    def __init__(self, detail: str = "provider rate limited", retry_after: int | None = None):
        super().__init__(detail)
        self.retry_after = retry_after if retry_after is not None and retry_after >= 0 else None


class ContextLimitError(ValueError):
    """The request exceeds a known context limit."""


class ProviderInternalError(RuntimeError):
    """An unexpected provider-side failure with no safe public detail."""
