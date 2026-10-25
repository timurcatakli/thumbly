import asyncio
import time
from uuid import uuid4

import pytest

from thumbly.application.control_desk import ControlDesk
from thumbly.domain.errors import ThumblyError
from thumbly.domain.models import Action


async def terminal(service, op):
    async with asyncio.timeout(3):
        while (result := service.phone.operation_status(op, "owner"))["state"] in {"QUEUED", "DISPATCHED"}:
            await asyncio.sleep(0.005)
    return result


async def test_duplicate_once_and_observation_order(service, lease):
    op = str(uuid4())
    action = Action(kind="key", key="enter")
    service.phone.action(op, "owner", lease, action)
    assert service.phone.action(op, "owner", lease, action)["id"] == op
    frame = await service.phone.screenshot()
    assert (await terminal(service, op))["state"] == "SUCCEEDED"
    reports = [r for kind, r in service.phone.device.reports if kind == "keyboard" and 40 in r]
    assert len(reports) == 1 and frame.metadata.frame_id


async def test_pause_bypasses_blocked_capture(service, lease):
    service.phone.device.delay = 10
    capture = asyncio.create_task(service.phone.screenshot())
    await asyncio.sleep(0.02)
    op = str(uuid4())
    service.phone.action(op, "owner", lease, Action(kind="key", key="enter"))
    async with asyncio.timeout(0.2):
        await service.phone.pause()
    assert service.phone.operation_status(op, "owner")["state"] == "CANCELLED"
    assert service.phone.device.generation is None
    capture.cancel()


def count_wakes(coordinator):
    """The watcher clears its event once per loop turn, so each call is one wake-up."""
    wakes = []
    clear = coordinator.changed.clear
    coordinator.changed.clear = lambda: (wakes.append(time.monotonic()), clear())[1]
    return wakes


async def test_watcher_never_wakes_without_a_lease(service):
    wakes = count_wakes(service.coordinator)
    await asyncio.sleep(0.3)
    assert wakes == [] and not service.coordinator.watcher.done()


async def test_idle_lease_expires_once_and_releases_input(service):
    coordinator = service.coordinator
    coordinator.lease_seconds = 0.2
    lease = (await service.phone.acquire_control("owner"))["lease_token"]
    before = service.phone.device.releases
    await asyncio.sleep(0.1)
    assert coordinator.lease is not None
    await asyncio.sleep(0.25)
    assert coordinator.lease is None and service.phone.device.releases == before + 1
    # Back to waiting on the event alone: no more wake-ups and no second release.
    wakes = count_wakes(coordinator)
    await asyncio.sleep(0.3)
    assert wakes == [] and service.phone.device.releases == before + 1
    with pytest.raises(ThumblyError) as expired:
        service.phone.action(str(uuid4()), "owner", lease, Action(kind="key", key="enter"))
    assert expired.value.code == "LEASE_EXPIRED"
    assert not [row for row in service.journal.list_operations() if row["kind"] == "key"]


async def test_actions_and_captures_move_the_deadline(service):
    coordinator = service.coordinator
    coordinator.lease_seconds = 0.3
    lease = (await service.phone.acquire_control("owner"))["lease_token"]
    await asyncio.sleep(0.2)
    op = str(uuid4())
    service.phone.action(op, "owner", lease, Action(kind="key", key="enter"))
    assert (await terminal(service, op))["state"] == "SUCCEEDED"
    await asyncio.sleep(0.2)  # Past the acquire deadline, inside the action's.
    assert coordinator.lease is not None
    await service.phone.screenshot()
    coordinator.accept("owner", lease)
    expires = coordinator.lease.expires
    # A capture with someone else's token, or a wrong one, changes nothing.
    coordinator.accept("other", lease)
    coordinator.accept("owner", "stale")
    assert coordinator.lease.expires == expires
    await asyncio.sleep(0.2)  # Past the action's deadline, inside the capture's.
    assert coordinator.lease is not None
    await asyncio.sleep(0.3)
    assert coordinator.lease is None


async def test_in_flight_work_holds_the_lease(service):
    coordinator = service.coordinator
    coordinator.lease_seconds = 0.15
    lease = (await service.phone.acquire_control("owner"))["lease_token"]
    service.phone.device.delay = 0.5
    capture = asyncio.create_task(service.phone.screenshot())
    await asyncio.sleep(0.01)
    op = str(uuid4())
    service.phone.action(op, "owner", lease, Action(kind="key", key="enter"))
    await asyncio.sleep(0.3)  # Deadline passed while the action waits behind the capture.
    assert coordinator.lease is not None and coordinator.lease_status("owner")["owned"]
    coordinator.check("owner", lease)
    await capture
    service.phone.device.delay = 0.05
    assert (await terminal(service, op))["state"] == "SUCCEEDED"
    # The deadline counts from the action's end.
    await asyncio.sleep(0.05)
    assert coordinator.lease is not None
    await asyncio.sleep(0.3)
    assert coordinator.lease is None


async def test_release_frees_at_once_and_busy_until_then(service, lease):
    with pytest.raises(ThumblyError) as busy:
        await service.phone.acquire_control("other")
    assert busy.value.code == "CONTROL_BUSY"
    assert service.coordinator.lease_status("other")["remaining_seconds"] > 80
    await service.phone.release_control("owner", lease)
    other = (await service.phone.acquire_control("other"))["lease_token"]
    assert other != lease and service.coordinator.lease.owner == "other"


async def test_pause_still_works_and_does_not_stop_expiry(service):
    coordinator = service.coordinator
    coordinator.lease_seconds = 0.2
    await service.phone.acquire_control("owner")
    await service.phone.pause()  # Owner override; needs no lease.
    assert coordinator.paused and coordinator.lease is not None
    await asyncio.sleep(0.35)
    assert coordinator.lease is None and coordinator.paused


async def test_failed_expiry_does_not_kill_the_watcher(service, monkeypatch):
    coordinator = service.coordinator
    coordinator.lease_seconds = 0.1
    stop_input = coordinator.stop_input
    calls = []

    async def failing():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("release failed")
        await stop_input()

    monkeypatch.setattr(coordinator, "stop_input", failing)
    await service.phone.acquire_control("owner")
    await asyncio.sleep(0.25)
    assert calls == [1] and coordinator.lease is None and not coordinator.watcher.done()
    await service.phone.acquire_control("owner")
    await asyncio.sleep(0.25)
    assert calls == [1, 1] and coordinator.lease is None


async def test_close_leaves_no_task_running(service):
    coordinator = ControlDesk(service.journal, service.phone.device, lease_seconds=60)
    await coordinator.start()
    await coordinator.acquire("owner")
    await coordinator.close()
    watcher, runner = coordinator.watcher, coordinator.runner
    assert watcher is not None and runner is not None
    assert watcher.done() and runner.done()


async def test_cancel_dispatched_is_unknown(service, lease):
    op = str(uuid4())
    service.phone.action(op, "owner", lease, Action(kind="type_text", text="lots of characters"))
    await asyncio.sleep(0.07)
    await service.phone.cancel(op, "owner")
    assert (await terminal(service, op))["state"] == "OUTCOME_UNKNOWN"
    assert service.phone.device.generation is None


async def test_capture_failure_invalidates_pointer_frame(service, lease):
    frame = await service.phone.screenshot()
    service.phone.device.failure = ThumblyError("TRUST_REQUIRED")
    with pytest.raises(ThumblyError):
        await service.phone.screenshot()
    with pytest.raises(ThumblyError, match="screenshot"):
        service.phone.action(
            str(uuid4()),
            "owner",
            lease,
            Action(
                kind="tap",
                x=0.5,
                y=0.5,
                frame_id=frame.metadata.frame_id,
                geometry_epoch=frame.metadata.geometry_epoch,
            ),
        )


async def test_device_failure_reported_independently_of_network(service):
    service.phone.device.failure = ThumblyError("DEVICE_DISCONNECTED")
    status = await service.phone.status()
    assert status["components"]["device"]["state"] == "unavailable"
    assert status["connection"]["state"] == "connected" and status["transport"] == "usb"
    assert "network" in status["components"]


async def test_queue_bounded(service, lease):
    service.phone.device.delay = 0.3
    task = asyncio.create_task(service.phone.screenshot())
    await asyncio.sleep(0.01)
    results = [
        service.phone.action(str(uuid4()), "owner", lease, Action(kind="key", key="enter")) for _ in range(20)
    ]
    assert sum(r["state"] == "FAILED" for r in results) == 4
    await service.phone.pause()
    await task


async def test_launch_app_foregrounds_without_hid(service, lease):
    op = str(uuid4())
    before = len(service.phone.device.reports)
    service.phone.action(op, "owner", lease, Action(kind="launch_app", bundle_id="com.apple.mobilenotes"))
    result = await terminal(service, op)
    assert result["state"] == "SUCCEEDED"
    assert result["result"]["process"]["bundle_id"] == "com.apple.mobilenotes"
    assert list(service.phone.device.reports)[before:] == [("launch_app", "com.apple.mobilenotes")]
    assert service.phone.device.generation is None  # no HID generation was opened

    missing = str(uuid4())
    service.phone.action(missing, "owner", lease, Action(kind="launch_app", bundle_id="com.example.absent"))
    failed = await terminal(service, missing)
    assert failed["state"] == "OUTCOME_UNKNOWN" and failed["result"]["code"] == "APP_NOT_FOUND"

    with pytest.raises(ValueError):
        Action(kind="launch_app", bundle_id="notes")
    assert "launch_app" in service.phone.capabilities()["actions"]


async def test_send_text_sets_clipboard_then_pastes(service, lease):
    op = str(uuid4())
    before = len(service.phone.device.reports)
    text = "Pasteboard test 中文 🚀\nline2"
    service.phone.action(op, "owner", lease, Action(kind="send_text", text=text))
    result = await terminal(service, op)
    assert result["state"] == "SUCCEEDED"
    assert result["result"]["clipboard_replaced"] is True and result["result"]["characters"] == len(text)
    new = list(service.phone.device.reports)[before:]
    assert new[0] == ("clipboard_set", len(text))
    assert ("keyboard", [0xE3, 0x19]) in new  # Cmd held, then V
    assert (await service.phone.clipboard())["text"] == text

    with pytest.raises(ValueError):
        Action(kind="send_text", text="")
    with pytest.raises(ThumblyError, match="ASCII"):
        service.phone.action(str(uuid4()), "owner", lease, Action(kind="type_text", text="x" * 201))


async def test_open_url_routes_web_to_safari_and_schemes_to_apps(service, lease):
    op = str(uuid4())
    service.phone.action(op, "owner", lease, Action(kind="open_url", url="https://example.net/"))
    assert (await terminal(service, op))["state"] == "SUCCEEDED"
    assert (
        "open_url",
        "com.apple.mobilesafari",
        "https://example.net/",
        False,
    ) in service.phone.device.reports

    op = str(uuid4())
    service.phone.action(
        op,
        "owner",
        lease,
        Action(kind="open_url", url="notes://show?id=1", bundle_id="com.apple.mobilenotes"),
    )
    assert (await terminal(service, op))["state"] == "SUCCEEDED"
    assert ("open_url", "com.apple.mobilenotes", "notes://show?id=1", True) in service.phone.device.reports

    for bad in ("instagram://user?username=x", "example.net", "https://a b"):
        with pytest.raises(ValueError):
            Action(kind="open_url", url=bad)


async def test_open_link_resolves_profile_and_opens_url(service, lease):
    op = str(uuid4())
    service.phone.open_link(
        op, "owner", lease, "com.apple.mobilesafari", "page", {"url": "https://example.net/"}
    )
    assert (await terminal(service, op))["state"] == "SUCCEEDED"
    assert (
        "open_url",
        "com.apple.mobilesafari",
        "https://example.net/",
        False,
    ) in service.phone.device.reports
    with pytest.raises(ThumblyError, match="No app profile"):
        service.phone.open_link(str(uuid4()), "owner", lease, "com.example.none", "page", {})
    apps = await service.phone.apps()
    assert {a["bundle_id"]: a["has_profile"] for a in apps}["com.apple.mobilesafari"] is True
    assert (
        service.phone.app_profile("com.burbn.instagram")["workflows"]["send_dm"]["steps"][0]["kind"] == "open"
    )


class Checked(asyncio.Future):
    """A work future that records the journal state at the moment it settles."""

    def __init__(self, journal, op, seen):
        super().__init__()
        self.journal, self.op, self.seen = journal, op, seen

    def _record(self):
        self.seen.append(self.journal.get(self.op, "owner")["state"])

    def set_result(self, result):
        self._record()
        super().set_result(result)

    def cancel(self, msg=None):
        if not self.done():
            self._record()
        return super().cancel(msg)


def checked(service, op, seen):
    work = service.coordinator.pending[op]
    work.future = Checked(service.journal, op, seen)
    return work


async def test_journal_is_written_before_the_work_future_settles(service, lease):
    """Coordinator.outcome waiters read the journal once the future settles: every path must
    have recorded a terminal state by then."""
    seen: list[str] = []
    phone = service.phone
    # Success.
    op = str(uuid4())
    phone.action(op, "owner", lease, Action(kind="key", key="enter"))
    await service.coordinator.outcome(checked(service, op, seen).operation_id, 3)
    # Failure while running.
    op = str(uuid4())
    phone.action(op, "owner", lease, Action(kind="key", key="enter"))
    checked(service, op, seen)
    phone.device.connected = False
    await service.coordinator.outcome(op, 3)
    phone.device.connected = True
    # Cancelled while queued, and cancelled while running.
    phone.device.delay = 0.5
    capture = asyncio.create_task(phone.screenshot())
    await asyncio.sleep(0.01)
    queued = str(uuid4())
    phone.action(queued, "owner", lease, Action(kind="key", key="enter"))
    checked(service, queued, seen)
    await phone.cancel(queued, "owner")
    await capture
    running = str(uuid4())
    phone.action(running, "owner", lease, Action(kind="type_text", text="lots of characters"))
    checked(service, running, seen)
    await asyncio.sleep(0.07)
    await phone.pause()
    await service.coordinator.outcome(running, 3)
    assert seen == ["SUCCEEDED", "OUTCOME_UNKNOWN", "CANCELLED", "OUTCOME_UNKNOWN"], seen


async def test_outcome_waits_without_cancelling_the_work(service, lease):
    op = str(uuid4())
    service.phone.action(op, "owner", lease, Action(kind="type_text", text="lots of characters"))
    started = time.monotonic()
    await service.coordinator.outcome(op, 0.05)
    assert time.monotonic() - started < 0.5
    assert service.phone.operation_status(op, "owner")["state"] in {"QUEUED", "DISPATCHED"}
    waiter = asyncio.create_task(service.coordinator.outcome(op, 10))
    await asyncio.sleep(0.05)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    await service.coordinator.outcome(op, 10)
    assert service.phone.operation_status(op, "owner")["state"] == "SUCCEEDED"
    await service.coordinator.outcome(str(uuid4()), 10)  # Unknown or finished: returns at once.
