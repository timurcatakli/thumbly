"""Paced gestures: the worker's clock drives position updates; late reports skip, never burst."""

import asyncio

import pytest

from thumbly.application.actions import SWIPE_RATE, swipe_path
from thumbly.coredevice import hid
from thumbly.domain.errors import ThumblyError
from thumbly.domain.models import Action


class FakeClock:
    """Virtual time: ``sleep`` advances it, and every ``send`` costs ``send_cost`` seconds."""

    def __init__(self, send_cost):
        self.now = 100.0
        self.send_cost = send_cost
        self.sent = []

    def clock(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds

    async def send(self, state, x, y):
        self.sent.append((round(self.now - 100.0, 3), state, y))
        self.now += self.send_cost


def path(seconds=0.6, hold=0.4, dwell=0.35):
    action = Action(
        kind="swipe",
        x=0.5,
        y=0.8,
        x2=0.5,
        y2=0.3,
        seconds=seconds,
        hold=hold,
        frame_id="f",
        geometry_epoch="g",
    )
    return swipe_path(action, SWIPE_RATE, dwell)


def test_swipe_path_shape():
    points = path()
    assert points[0][:2] == [0.0, "contact"] and points[-1][1] == "release"
    assert len(points) == 1 + round(0.6 * SWIPE_RATE) + 1
    times = [p[0] for p in points]
    assert times == sorted(times) and times[1] == pytest.approx(0.35 + 1 / SWIPE_RATE, abs=1e-3)
    assert times[-1] == pytest.approx(0.35 + 0.6 + 0.4)
    ys = [p[3] for p in points]
    assert ys[0] > ys[-1] and all(a >= b for a, b in zip(ys, ys[1:]))  # moves up monotonically
    hid.validate_points(points)


async def test_pace_keeps_the_schedule_when_sends_are_fast():
    fake = FakeClock(send_cost=0.005)
    points = path()
    sent = await hid.pace(points, fake.send, clock=fake.clock, sleep=fake.sleep)
    assert len(sent) == len(points)
    # Each report went out at its scheduled time (within the send cost).
    for (actual, _, _), point in zip(fake.sent, points):
        assert abs(actual - point[0]) <= 0.006
    assert fake.sent[-1][1] == "release"


async def test_pace_skips_stale_points_instead_of_bursting():
    fake = FakeClock(send_cost=0.2)  # six times slower than the 30 Hz schedule
    points = path()
    sent = await hid.pace(points, fake.send, clock=fake.clock, sleep=fake.sleep)
    states = [s for _, s, _ in fake.sent]
    assert states[0] == "contact" and states[-1] == "release" and states.count("release") == 1
    # Far fewer reports than points, the gesture still ends on time, and positions stay ordered.
    assert 4 <= len(sent) <= 9 and sent[-1] == pytest.approx(points[-1][0], abs=0.25)
    ys = [y for _, _, y in fake.sent]
    assert all(a >= b for a, b in zip(ys, ys[1:]))
    # No two reports closer together than one send: no catch-up burst.
    assert all(b - a >= 0.2 - 1e-9 for a, b in zip(sent, sent[1:]))


async def test_pace_runs_on_the_real_loop_quickly():
    points = path(seconds=0.1, hold=0.05, dwell=0.0)
    seen = []

    async def send(state, x, y):
        seen.append(state)

    sent = await asyncio.wait_for(hid.pace(points, send), 2)
    assert seen[0] == "contact" and seen[-1] == "release" and len(sent) == len(points)


def test_validate_points_rejects_bad_paths():
    good = path()
    for bad in (
        good[:-1],  # no release
        [[0.0, "contact", 1, 1], [-1.0, "release", 1, 1]],  # time goes backwards
        [[0.0, "contact", 70000, 1], [0.1, "release", 1, 1]],  # axis out of range
        [[0.0, "contact", 1, 1], [hid.MAX_GESTURE_SECONDS + 1, "release", 1, 1]],  # too long
        [],
    ):
        with pytest.raises(ThumblyError):
            hid.validate_points(bad)
