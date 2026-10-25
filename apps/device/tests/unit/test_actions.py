import asyncio

import pytest

from thumbly.application.actions import ASCII, MODIFIERS, chord, execute, plan
from thumbly.domain.errors import ThumblyError
from thumbly.domain.geometry import geometry_epoch, touch_point
from thumbly.domain.models import Action


def test_geometry_edges_and_portrait():
    assert touch_point(0, 1) == (0, 65535)
    assert touch_point(0.5, 0.5) == (32768, 32768)
    for x in (-0.1, 1.1, float("nan"), float("inf")):
        with pytest.raises(ThumblyError):
            touch_point(x, 0.5)
    with pytest.raises(ThumblyError):
        geometry_epoch(844, 390, "phone")
    assert geometry_epoch(390, 844, "a") != geometry_epoch(390, 844, "b")


def test_ascii_complete_and_keyboard_usages_only():
    assert set(ASCII) == {chr(i) for i in range(32, 127)}
    assert ASCII["#"] == (True, 32)
    events = plan(Action(kind="type_text", text="A#z !"))
    assert len(events) == 4 + 4 + 2 + 2 + 4
    for kind, value, _ in events:
        assert kind == "keyboard" and all(0 < usage <= 0xE7 for usage in value)
    assert [v for _, v, _ in events[:4]] == [
        [MODIFIERS["shift"]],
        [MODIFIERS["shift"], 4],
        [MODIFIERS["shift"]],
        [],
    ]
    assert events[8][1] == [29] and events[9][1] == []


async def test_unsupported_text_before_any_report(service, lease):
    before = len(service.phone.device.reports)
    with pytest.raises(ThumblyError, match="ASCII"):
        service.phone.action("any", "owner", lease, Action(kind="type_text", text="hello💥"))
    assert len(service.phone.device.reports) == before


def test_chord_staged():
    reports = [r for _, r, _ in chord("a", ["cmd", "shift"])]
    assert reports == [[0xE3, 0xE1], [0xE3, 0xE1, 4], [0xE3, 0xE1], []]
    with pytest.raises(ThumblyError):
        chord("fn", [])


def test_button_events_hold_then_release():
    events = plan(Action(kind="button", button="lock"))
    assert events == [("button", [0x0C, 0x30, "down"], 0.5), ("button", [0x0C, 0x30, "up"], 0.05)]
    with pytest.raises(ValueError):
        Action(kind="button", button="power")


async def test_release_on_cancel(service):
    task = asyncio.create_task(
        execute(
            service.phone.device,
            service.phone.binding(),
            [("keyboard", [0xE3, 4], 2)],
            lambda: None,
        )
    )
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.phone.device.generation is None
    assert service.phone.device.reports[-2] == ("keyboard", [])


def test_tap_is_a_short_contact_then_release():
    tap = plan(Action(kind="tap", x=0.5, y=0.5, frame_id="f", geometry_epoch="g"))
    assert [v[0] for _, v, _ in tap] == ["contact", "release"] and tap[0][2] == 0.08
