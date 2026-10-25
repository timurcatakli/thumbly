"""Report validation and the generation/watchdog gate that stops input after the API goes away."""

import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable

from thumbly.domain.errors import ThumblyError
from thumbly.domain.geometry import TOUCH_MAX

WATCHDOG_SECONDS = 2.5
MAX_HELD_KEYS = 8
# One gesture: at most this many position updates over at most this many seconds.
MAX_POINTS, MAX_GESTURE_SECONDS = 300, 6.0


class ReportGate:
    def __init__(self):
        self.generation = None
        self.last_report = 0.0

    def release(self):
        self.generation = None

    def begin(self):
        self.generation = secrets.token_hex(16)
        self.last_report = time.monotonic()
        return self.generation

    def expired(self, now=None):
        return self.generation is not None and (now or time.monotonic()) - self.last_report > WATCHDOG_SECONDS

    def accept(self, generation, kind, value):
        if not self.generation or generation != self.generation:
            raise ThumblyError("INPUT_INTERRUPTED")
        if self.expired():
            self.release()
            raise ThumblyError("INPUT_EXPIRED")
        validate(kind, value)
        self.last_report = time.monotonic()


def validate(kind, value):
    if not isinstance(value, list):
        raise ThumblyError("INVALID_REPORT")
    if kind == "touch":
        if len(value) != 3 or value[0] not in {"contact", "release"}:
            raise ThumblyError("INVALID_REPORT")
        if not all(isinstance(v, int) and 0 <= v <= TOUCH_MAX for v in value[1:]):
            raise ThumblyError("INVALID_REPORT")
    elif kind == "keyboard":
        if len(value) > MAX_HELD_KEYS or not all(isinstance(v, int) and 0 < v <= 0xE7 for v in value):
            raise ThumblyError("INVALID_REPORT")
    elif kind == "button":
        if len(value) != 3 or value[2] not in {"down", "up"}:
            raise ThumblyError("INVALID_REPORT")
        if not all(isinstance(v, int) and 0 <= v <= 0xFFFF for v in value[:2]):
            raise ThumblyError("INVALID_REPORT")
    else:
        raise ThumblyError("INVALID_REPORT")


async def pace(
    points: list,
    send: Callable[[str, int, int], Awaitable[None]],
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> list[float]:
    """Send one touch gesture as scheduled position updates.

    ``points`` are ``[t, state, x, y]`` with ``t`` seconds from the gesture start, in order,
    ending with the release. Each report waits for its time; when a report comes back late,
    the contact points that are already due are skipped (the finger jumps to the newest due
    position) instead of being sent as a burst, so the gesture keeps its real duration. The
    release is always sent. Returns the actual send times, seconds from the start."""
    start = clock()
    sent: list[float] = []
    index, last = 0, len(points) - 1
    while index <= last:
        t, state, x, y = points[index]
        now = clock() - start
        if now < t:
            await sleep(t - now)
            now = clock() - start
        # Skip stale contact points: keep only the newest one already due. Never skip a release.
        while index < last and points[index + 1][0] <= now and points[index + 1][1] == "contact":
            index += 1
            t, state, x, y = points[index]
        await send(state, x, y)
        sent.append(round(clock() - start, 4))
        index += 1
    return sent


def validate_points(points) -> list:
    """Shape-check a gesture path: ordered times, 16-bit axes, a release last."""
    if not isinstance(points, list) or not 2 <= len(points) <= MAX_POINTS:
        raise ThumblyError("INVALID_REPORT")
    previous = -1.0
    for point in points:
        if not isinstance(point, list) or len(point) != 4:
            raise ThumblyError("INVALID_REPORT")
        t, state, x, y = point
        if not isinstance(t, (int, float)) or t < previous or t > MAX_GESTURE_SECONDS:
            raise ThumblyError("INVALID_REPORT")
        validate("touch", [state, x, y])
        previous = float(t)
    if points[-1][1] != "release":
        raise ThumblyError("INVALID_REPORT")
    return points
