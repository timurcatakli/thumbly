"""The only image-normalized to touch-report transform. No Safari offset enters this path."""

import hashlib
import math

from thumbly.domain.errors import ThumblyError

TOUCH_MAX = 65535  # CoreDevice touchscreen reports are resolution independent 16-bit axes.


def geometry_epoch(width: int, height: int, device: str) -> str:
    if width >= height or width < 1:
        raise ThumblyError("GEOMETRY_INCOMPATIBLE", "Lock the phone in portrait orientation")
    return hashlib.sha256(f"{device}:{width}:{height}:portrait:v1".encode()).hexdigest()[:24]


def touch_point(x: float, y: float) -> tuple[int, int]:
    if not all(math.isfinite(v) and 0 <= v <= 1 for v in (x, y)):
        raise ThumblyError("INVALID_COORDINATES")
    return round(x * TOUCH_MAX), round(y * TOUCH_MAX)
