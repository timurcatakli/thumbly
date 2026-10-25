"""Exceptions raised by the SDK, one class per thing a script may want to handle."""

from typing import Any


class ThumblyError(Exception):
    """An error reported by Thumbly or the SDK; ``code`` is Thumbly's stable error code."""

    def __init__(self, code: str, message: str = "", *, detail: dict[str, Any] | None = None):
        super().__init__(f"{code}: {message}" if message else code)
        self.code, self.message, self.detail = code, message, detail or {}

    @property
    def dispatched(self) -> bool | None:
        """False when Thumbly refused the request before anything reached the phone."""
        value = self.detail.get("dispatched")
        return value if isinstance(value, bool) else None

    @property
    def operation_id(self) -> str | None:
        return self.detail.get("operation_id")


class ApprovalRequired(ThumblyError):
    """No valid grant and approval is not allowed here (no terminal, or allow_approval=False)."""


class ApprovalDenied(ThumblyError):
    """The owner denied the request in the Thumbly app."""


class ApprovalExpired(ThumblyError):
    """The request lapsed before the owner decided, or the approval wait timed out."""


class ControlBusy(ThumblyError):
    """Another client controls the phone right now."""


class Paused(ThumblyError):
    """Input is paused in Thumbly; call ``resume()`` only to unpause on purpose."""


class RateLimited(ThumblyError):
    """Too many calls per minute; the SDK already backed off and retried."""


class FrameExpired(ThumblyError):
    """The frame a touch refers to is no longer Thumbly's current one; take a screenshot."""


class OutcomeUnknown(ThumblyError):
    """Thumbly cannot say whether the input reached the phone; check the screen, do not replay."""


class OperationFailed(ThumblyError):
    """Thumbly ran the operation and it failed or was cancelled."""


class ScopeMissing(ThumblyError):
    """The grant does not include what this call needs."""


class ResponseTooLarge(ThumblyError):
    """Thumbly answered with more bytes than the SDK accepts."""


BY_CODE: dict[str, type[ThumblyError]] = {
    "CONTROL_BUSY": ControlBusy,
    "PAUSED": Paused,
    "RATE_LIMITED": RateLimited,
    "FRAME_EXPIRED": FrameExpired,
    "OUTCOME_UNKNOWN": OutcomeUnknown,
    "SCOPE_REQUIRED": ScopeMissing,
}


def from_payload(error: dict[str, Any]) -> ThumblyError:
    code = str(error.get("code") or "UNKNOWN")
    message = str(error.get("message") or error.get("instruction") or "")
    return BY_CODE.get(code, ThumblyError)(code, message, detail=error)
