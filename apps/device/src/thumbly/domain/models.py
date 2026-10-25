from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def identifier() -> str:
    return str(uuid4())


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


Transport = Literal["usb", "wifi", "auto"]

BUTTONS = ("home", "lock", "volume_up", "volume_down", "mute", "siri")
TYPE_TEXT_LIMIT = 200  # keystrokes through the virtual keyboard
SEND_TEXT_LIMIT = 10_000  # characters through the clipboard
SEND_TEXT_BYTES = 16_384  # UTF-8 bytes through the clipboard, whatever the character count
LINK_PARAMS = 8  # a profile deep link declares at most this many placeholders
LINK_PARAM_KEY, LINK_PARAM_VALUE = 40, 2048  # the filled URL is capped at 2048 characters anyway


class Binding(StrictModel):
    """The explicitly owner-confirmed phone and the CoreDevice transport it should use.

    ``auto`` uses USB whenever the bound phone is on the cable and Wi-Fi otherwise, switching
    between them on its own; ``usb`` and ``wifi`` pin one path."""

    udid: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9-]+$")
    transport: Transport = "auto"
    owner_confirmed: Literal[True]
    portrait_locked: Literal[True]


class CaptureOptions(StrictModel):
    format: Literal["png", "jpeg"] = "jpeg"
    max_size: int | None = Field(default=None, ge=128, le=4096)


FULL_PAGE_MAX_PAGES = 20  # bounds time and memory; the default reads to the end


class FullPageOptions(StrictModel):
    """Scroll a screen and return every page plus one joined image.

    Thumbly scrolls until the content stops moving or ``max_pages`` is reached. ``max_size``
    bounds the long edge of each page (and so the width of the joined image). After each drag
    Thumbly waits for the screen to change (input can take seconds to reach the phone) and
    then for it to hold still for ``settle`` seconds before the capture; ``stitch`` turns the
    joined image off when only the pages are wanted."""

    format: Literal["png", "jpeg"] = "jpeg"
    max_size: int = Field(default=1536, ge=128, le=2048)
    max_pages: int = Field(default=FULL_PAGE_MAX_PAGES, ge=1, le=FULL_PAGE_MAX_PAGES)
    settle: float = Field(default=0.5, ge=0, le=5)
    stitch: bool = True


class FrameMetadata(StrictModel):
    frame_id: str = Field(default_factory=identifier)
    stream_epoch: str = Field(default_factory=identifier)
    geometry_epoch: str
    width: int
    height: int
    native_width: int
    native_height: int
    orientation: Literal["portrait"] = "portrait"
    content_rect: tuple[int, int, int, int]
    received_at: float
    request_seconds: float
    source_timestamp: float | None = None
    source_clock: str | None = None
    source_age_seconds: float | None = None
    media_type: str


@dataclass
class Frame:
    metadata: FrameMetadata
    image: bytes


class Action(StrictModel):
    kind: Literal[
        "tap",
        "long_press",
        "swipe",
        "type_text",
        "key",
        "shortcut",
        "button",
        "launch_app",
        "send_text",
        "open_url",
    ]
    frame_id: str | None = None
    geometry_epoch: str | None = None
    x: float | None = Field(default=None, ge=0, le=1)
    y: float | None = Field(default=None, ge=0, le=1)
    x2: float | None = Field(default=None, ge=0, le=1)
    y2: float | None = Field(default=None, ge=0, le=1)
    seconds: float = Field(default=0.6, ge=0.03, le=2)
    # Swipe only: rest at the end point this long before lifting, so the drag has no momentum
    # and the content stops exactly where the finger stopped.
    hold: float = Field(default=0, ge=0, le=2)
    text: str = Field(default="", max_length=SEND_TEXT_LIMIT)
    key: str = Field(default="", max_length=30)
    modifiers: list[Literal["ctrl", "shift", "alt", "cmd"]] = Field(default_factory=list, max_length=4)
    shortcut: str = Field(default="", max_length=30)
    button: str = Field(default="", max_length=20)
    bundle_id: str = Field(default="", max_length=255, pattern=r"^[A-Za-z0-9.\-]*$")
    url: str = Field(default="", max_length=2048)
    # open_url only: restart the app before opening the link (an app profile may say not to).
    cold_launch: bool = True

    @model_validator(mode="after")
    def coordinates(self):
        if self.kind in {"tap", "long_press", "swipe"}:
            if any(v is None for v in (self.x, self.y, self.frame_id, self.geometry_epoch)):
                raise ValueError("Touch actions require coordinates and a frame/geometry reference")
            if self.kind == "swipe" and (self.x2 is None or self.y2 is None):
                raise ValueError("Swipe requires end coordinates")
        if self.kind == "button" and self.button not in BUTTONS:
            raise ValueError("Unknown hardware button")
        if self.kind == "send_text" and not self.text:
            raise ValueError("send_text requires text")
        if self.kind == "open_url":
            scheme, sep, rest = self.url.partition(":")
            if not sep or not rest or not all(c.isalnum() or c in "+-." for c in scheme):
                raise ValueError("open_url requires an absolute URL with a scheme")
            if any(c.isspace() or ord(c) < 32 for c in self.url):
                raise ValueError("open_url rejects whitespace and control characters")
            if scheme.lower() not in {"http", "https"} and "." not in self.bundle_id:
                raise ValueError("open_url needs bundle_id for schemes other than http and https")
        if self.kind == "launch_app" and "." not in self.bundle_id:
            raise ValueError("launch_app requires a bundle identifier such as com.apple.Preferences")
        return self


class MutationRequest(StrictModel):
    operation_id: str = Field(min_length=36, max_length=36, pattern=r"^[0-9a-fA-F-]+$")
    lease_token: str | None = Field(default=None, max_length=128)


class ActionRequest(MutationRequest):
    action: Action


class LinkRequest(MutationRequest):
    bundle_id: str = Field(max_length=255, pattern=r"^[A-Za-z0-9.\-]+$")
    link: str = Field(max_length=40, pattern=r"^[a-z][a-z0-9_]*$")
    params: dict[
        Annotated[str, Field(max_length=LINK_PARAM_KEY)], Annotated[str, Field(max_length=LINK_PARAM_VALUE)]
    ] = Field(default_factory=dict, max_length=LINK_PARAMS)


class FullPageRequest(MutationRequest):
    options: FullPageOptions = Field(default_factory=FullPageOptions)


class SetupRequest(MutationRequest):
    payload: dict = Field(default_factory=dict)


class ControlRequest(MutationRequest):
    target: str | None = Field(default=None, min_length=36, max_length=36, pattern=r"^[0-9a-fA-F-]+$")
