from __future__ import annotations

from enum import Enum


class SubmitState(str, Enum):
    NOT_SUBMITTED = "NOT_SUBMITTED"
    SUBMITTING = "SUBMITTING"
    SUBMITTED_UNCERTAIN = "SUBMITTED_UNCERTAIN"
    COMPLETED = "COMPLETED"


class PreSubmitError(RuntimeError):
    """The request failed before an irreversible send gesture."""


class UncertainSubmitError(RuntimeError):
    """The provider may have received the request; automatic resend is unsafe."""

    def __init__(self, detail: str = "provider response was not observed"):
        super().__init__(
            "Submission result is uncertain; the provider may have received the message "
            f"and the result is unknown ({detail})"
        )
