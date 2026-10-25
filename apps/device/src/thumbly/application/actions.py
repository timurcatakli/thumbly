"""Validate complete input before dispatch; stage modifiers before the main key.

Events are transport-neutral tuples ``(kind, value, delay_seconds)``:

- ``("touch", [state, x, y], delay)`` with ``state`` ``contact`` or ``release`` and 16-bit axes
- ``("keyboard", [usage, ...], delay)`` the full set of held HID keyboard usages (empty = release)
- ``("button", [usage_page, usage, state], delay)`` a hardware button ``down`` or ``up``
"""

import asyncio

from thumbly.domain.capabilities import BUTTON_EVENTS, SHORTCUTS
from thumbly.domain.errors import ThumblyError
from thumbly.domain.geometry import touch_point
from thumbly.domain.models import TYPE_TEXT_LIMIT

# HID keyboard usage page 0x07. Modifiers are ordinary usages in the CoreDevice bitmap report.
MODIFIERS = {"ctrl": 0xE0, "shift": 0xE1, "alt": 0xE2, "cmd": 0xE3}
KEYS = {
    **{c: i + 4 for i, c in enumerate("abcdefghijklmnopqrstuvwxyz")},
    **{c: i + 30 for i, c in enumerate("1234567890")},
    "enter": 40,
    "escape": 41,
    "backspace": 42,
    "tab": 43,
    "space": 44,
    "right": 79,
    "left": 80,
    "down": 81,
    "up": 82,
    "delete": 76,
    "home": 74,
    "end": 77,
    "pageup": 75,
    "pagedown": 78,
    **{f"f{i + 1}": 58 + i for i in range(12)},
}
# Printable U.S. ASCII to (shift held, usage). Layout dependent: U.S. keyboard only.
ASCII = {c: (False, KEYS[c]) for c in "abcdefghijklmnopqrstuvwxyz1234567890"}
ASCII.update({c.upper(): (True, KEYS[c]) for c in "abcdefghijklmnopqrstuvwxyz"})
ASCII.update(
    {c: (False, code) for c, code in zip(" -=[]\\;'`,./", [44, 45, 46, 47, 48, 49, 51, 52, 53, 54, 55, 56])}
)
ASCII.update(
    {
        c: (True, code)
        for c, code in zip(
            '!@#$%^&*()_+{}|:"~<>?', [*range(30, 40), 45, 46, 47, 48, 49, 51, 52, 53, 54, 55, 56]
        )
    }
)


def keyboard(*usages: int):
    return "keyboard", list(usages)


def chord(key: str, modifiers: list[str]):
    if key.lower() not in KEYS or any(m not in MODIFIERS for m in modifiers):
        raise ThumblyError("UNSUPPORTED_KEY", status=400)
    held = [MODIFIERS[name] for name in modifiers]
    code = KEYS[key.lower()]
    return [
        (*keyboard(*held), 0.15),
        (*keyboard(*held, code), 0.15),
        (*keyboard(*held), 0.05),
        (*keyboard(), 0.02),
    ]


def touch(state: str, x: float, y: float):
    return "touch", [state, *touch_point(x, y)]


# Position updates per second for a drag sent as one gesture; the worker paces them.
SWIPE_RATE = 30


def swipe_path(action, rate: int = SWIPE_RATE, dwell: float = 0.05):
    """A drag as a timed path ``[t, state, x, y]``: contact at the start point, a rest of
    ``dwell`` seconds, moves at ``rate`` per second over ``action.seconds``, a rest of
    ``action.hold`` at the end point, then the release. Times are seconds from the start."""
    steps = max(1, int(round(action.seconds * rate)))
    x0, y0 = touch_point(action.x, action.y)
    points = [[0.0, "contact", x0, y0]]
    for i in range(1, steps + 1):
        fraction = i / steps
        x, y = touch_point(
            action.x + (action.x2 - action.x) * fraction, action.y + (action.y2 - action.y) * fraction
        )
        points.append([round(dwell + action.seconds * fraction, 4), "contact", x, y])
    x1, y1 = touch_point(action.x2, action.y2)
    points.append([round(dwell + action.seconds + action.hold, 4), "release", x1, y1])
    return points


async def execute_gesture(backend, binding, points, check_lease):
    """Send one touch gesture through the backend's paced path; release-all afterwards."""
    try:
        generation = await backend.begin(binding, ["touch"])
        check_lease()
        cadence = await backend.gesture(generation, points)
        return {"dispatched": True, "visual_confirmation_required": True, "gesture": cadence}
    finally:
        await backend.release_all()


def plan(action, validated_shortcuts=()):
    if action.kind == "type_text":
        if len(action.text) > TYPE_TEXT_LIMIT or any(c not in ASCII for c in action.text):
            raise ThumblyError("UNSUPPORTED_TEXT", "Use printable U.S. ASCII only", 400)
        events = []
        shift_usage = MODIFIERS["shift"]
        for char in action.text:
            shift, code = ASCII[char]
            if shift:
                # The virtual keyboard applies a modifier only when it was already held.
                events.extend(
                    [
                        (*keyboard(shift_usage), 0.03),
                        (*keyboard(shift_usage, code), 0.05),
                        (*keyboard(shift_usage), 0.03),
                        (*keyboard(), 0.03),
                    ]
                )
            else:
                events.extend([(*keyboard(code), 0.05), (*keyboard(), 0.05)])
        return events
    if action.kind == "key":
        return chord(action.key, action.modifiers)
    if action.kind == "shortcut":
        if action.shortcut not in validated_shortcuts or action.shortcut not in SHORTCUTS:
            raise ThumblyError("SHORTCUT_NOT_VALIDATED")
        return chord(*SHORTCUTS[action.shortcut])
    if action.kind == "button":
        if action.button not in BUTTON_EVENTS:
            raise ThumblyError("UNSUPPORTED_BUTTON", status=400)
        page, usage, hold = BUTTON_EVENTS[action.button]
        return [("button", [page, usage, "down"], hold), ("button", [page, usage, "up"], 0.05)]
    hold = 0.08 if action.kind == "tap" else action.seconds
    return [(*touch("contact", action.x, action.y), hold), (*touch("release", action.x, action.y), 0.05)]


async def execute(backend, binding, events, check_lease):
    try:
        generation = await backend.begin(binding, sorted({kind for kind, _, _ in events}))
        for kind, value, delay in events:
            check_lease()
            await backend.report(generation, kind, value)
            await asyncio.sleep(delay)
        return {"dispatched": True, "visual_confirmation_required": True}
    finally:
        await backend.release_all()
