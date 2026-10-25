"""Script an iPhone through Thumbly.

    from thumbly_sdk import Phone

    with Phone() as phone:  # http://127.0.0.1:8771, Thumbly on this Mac
        phone.open_app("com.apple.mobilenotes").type("hello").home()

The first run asks the owner to approve the script in the Thumbly app
(Access); afterwards the grant is cached and refreshed without
asking until the owner removes it.
"""

from thumbly_sdk.errors import (
    ApprovalDenied,
    ApprovalExpired,
    ApprovalRequired,
    ControlBusy,
    FrameExpired,
    OperationFailed,
    OutcomeUnknown,
    Paused,
    RateLimited,
    ResponseTooLarge,
    ScopeMissing,
    ThumblyError,
)
from thumbly_sdk.phone import Frame, Phone

__version__ = "0.1.0"

__all__ = [
    "ApprovalDenied",
    "ApprovalExpired",
    "ApprovalRequired",
    "ControlBusy",
    "Frame",
    "FrameExpired",
    "ThumblyError",
    "OperationFailed",
    "OutcomeUnknown",
    "Paused",
    "Phone",
    "RateLimited",
    "ResponseTooLarge",
    "ScopeMissing",
    "__version__",
]
