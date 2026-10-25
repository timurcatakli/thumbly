import asyncio
import base64
import json
from uuid import uuid4

import pytest

from thumbly.domain.errors import ThumblyError
from thumbly.interfaces.phone_channel import BridgeError, PhoneChannel


def local(service, **kwargs):
    return PhoneChannel(service.phone, owner=f"client:{uuid4()}", **kwargs)


async def frame_of(bridge):
    return (await bridge.screenshot())["metadata"]


async def test_errors_map_to_codes_and_dispatch_state(service, monkeypatch):
    bridge = local(service)
    # Invalid arguments: refused before anything was recorded, no input echoed.
    with pytest.raises(BridgeError) as invalid:
        await bridge.action(str(uuid4()), {"kind": "INVALID", "text": "private text"}, False)
    assert invalid.value.code == "INVALID_REQUEST" and invalid.value.public()["dispatched"] is False
    assert "private" not in json.dumps(invalid.value.public())
    # A 4xx state error from the core is a refusal too.
    with pytest.raises(BridgeError) as conflict:
        op = str(uuid4())
        await bridge.action(op, {"kind": "key", "key": "enter"}, False)
        await bridge.action(op, {"kind": "key", "key": "tab"}, False)
    assert conflict.value.code == "OPERATION_CONFLICT" and conflict.value.rejected
    # Anything else is not a refusal: the caller is told to inspect the operation, not replay it.
    op = str(uuid4())

    def broken(*args):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(service.phone, "action", broken)
    with pytest.raises(BridgeError) as failed:
        await bridge.action(op, {"kind": "key", "key": "enter"}, False)
    public = failed.value.public()
    assert failed.value.code == "BACKEND_FAILED" and "dispatched" not in public
    assert public["operation_id"] == op and "secret" not in json.dumps(public)
    monkeypatch.setattr(
        service.phone, "action", lambda *a: (_ for _ in ()).throw(ThumblyError("DOWN", status=503))
    )
    with pytest.raises(BridgeError) as down:
        await bridge.action(str(uuid4()), {"kind": "key", "key": "enter"}, False)
    assert down.value.status == 503 and not down.value.rejected

    # Reads map the same way and never carry an operation id.
    async def untrusted(binding):
        raise ThumblyError("TRUST_REQUIRED")

    monkeypatch.setattr(service.phone.device, "device_info", untrusted)
    with pytest.raises(BridgeError) as read:
        await bridge.device_info()
    assert read.value.code == "TRUST_REQUIRED" and read.value.public() == {"code": "TRUST_REQUIRED"}
    await bridge.close()


async def test_a_closed_bridge_refuses_every_call(service):
    bridge = local(service)
    await bridge.ensure_control()
    await bridge.close()
    assert service.coordinator.lease is None and bridge.lease is None
    with pytest.raises(BridgeError) as status:
        await bridge.status()
    assert status.value.code == "LOGIN_REQUIRED"
    with pytest.raises(BridgeError) as action:
        await bridge.action(str(uuid4()), {"kind": "key", "key": "enter"}, False)
    assert action.value.code == "LOGIN_REQUIRED" and action.value.public()["dispatched"] is False


async def test_dropping_the_bridge_mid_action_reports_the_true_outcome(service):
    bridge = local(service)
    await bridge.ensure_control()
    frame = await frame_of(bridge)
    op = str(uuid4())
    task = asyncio.create_task(
        bridge.action(
            op,
            {
                "kind": "long_press",
                "frame_id": frame["frame_id"],
                "geometry_epoch": frame["geometry_epoch"],
                "x": 0.5,
                "y": 0.5,
                "seconds": 2,
            },
            True,
        )
    )
    await asyncio.sleep(0.25)
    await bridge.close()  # what the gateway's sweep does on revocation
    result = await asyncio.wait_for(task, 1)
    assert result["operation"]["state"] == "OUTCOME_UNKNOWN" and "observation" not in result
    assert service.phone.device.generation is None and service.coordinator.lease is None


async def test_one_operation_id_sent_twice_at_once_dispatches_once(service):
    bridge = local(service)
    await bridge.ensure_control()
    before = len(service.phone.device.reports)
    op = str(uuid4())
    first, second = await asyncio.gather(
        bridge.action(op, {"kind": "key", "key": "enter"}, False),
        bridge.action(op, {"kind": "key", "key": "enter"}, False),
    )
    assert first["operation"]["state"] == second["operation"]["state"] == "SUCCEEDED"
    presses = [v for k, v in list(service.phone.device.reports)[before:] if k == "keyboard" and 40 in v]
    assert len(presses) == 1
    await bridge.close()


async def test_results_are_copies_of_what_the_box_keeps(service):
    bridge = local(service)
    data = await bridge.full_page(str(uuid4()), {"max_pages": 3, "settle": 0})
    assert data["operation"]["state"] == "SUCCEEDED"
    for page in data["pages"]:
        page.pop("image")  # what the MCP layer does with every image
    [kept] = service.phone.full_pages.values()
    assert all("image" in page for page in kept["pages"])
    await bridge.close()


async def test_outcome_waits_are_not_polled(service, monkeypatch):
    """A tap's wait ends when the coordinator finishes it, not at the next poll.

    Counted in event-loop turns, not seconds, so a slow runner cannot fail it: the tap is held at
    its last HID call, then let go, and the result must arrive before any timer could fire."""
    bridge = local(service)
    device = service.phone.device
    device.delay = 0
    await bridge.ensure_control()
    frame = await frame_of(bridge)
    held, gate = asyncio.Event(), asyncio.Event()
    release_all = device.release_all

    async def hold_last_release():
        # begin() also releases, before the tap has a generation; only the closing release waits.
        if device.generation is not None:
            held.set()
            await gate.wait()
        await release_all()

    monkeypatch.setattr(device, "release_all", hold_last_release)
    tap = {
        "kind": "tap",
        "frame_id": frame["frame_id"],
        "geometry_epoch": frame["geometry_epoch"],
        "x": 0.5,
        "y": 0.5,
    }
    action = asyncio.create_task(bridge.action(str(uuid4()), tap, False))
    await held.wait()
    gate.set()
    # The rest is in-loop work, a handful of turns. A poll would wait on a timer these turns
    # never reach.
    for _ in range(50):
        if action.done():
            break
        await asyncio.sleep(0)
    assert action.done()
    assert action.result()["operation"]["state"] == "SUCCEEDED"
    await bridge.close()


async def test_control_replays_and_reads(service):
    bridge = local(service)
    op = str(uuid4())
    first = await bridge._control("acquire", op)
    token = first["control"]["lease_token"]
    # A replayed acquire returns the same lease, token included, and journals nothing new.
    assert (await bridge._control("acquire", op))["control"]["lease_token"] == token
    shot = await bridge.screenshot()
    assert base64.b64decode(shot["image"]).startswith(b"\xff\xd8")
    assert shot["metadata"]["source_age_seconds"] is None and shot["metadata"]["content_rect"] == [
        0,
        0,
        390,
        844,
    ]
    status = await bridge.status()
    assert status["connection"]["state"] == "connected" and status["transport"] == "usb"
    assert status["control"]["owned"] is True and "lease_token" not in status["control"]
    assert (await bridge.apps())["apps"][0]["bundle_id"] == "com.apple.Preferences"
    assert (await bridge.apps("x" * 200))["query"] == "x" * 80
    assert (await bridge.device_info())["product_type"] == "Simulator"
    with pytest.raises(BridgeError) as cancel:
        await bridge._control("cancel", str(uuid4()))
    assert cancel.value.code == "INVALID_REQUEST" and cancel.value.status == 400


async def wait_for(bridge, op, seconds=2.0):
    await bridge.coordinator.outcome(op, seconds)
    return await bridge.operation(op)


async def test_discover_and_onboarding_setup_kinds(service):
    bridge = local(service)
    discovered = await bridge.discover()
    assert discovered["usb"] == [{"udid": "simulated-phone", "connection": "USB"}]
    assert discovered["wifi_paired"] == [] and discovered["simulated"] is True
    await bridge.ensure_control()
    for kind in ("pair", "developer_mode"):
        for payload in (
            {"udid": "simulated-phone"},
            {"udid": "simulated-phone", "owner_confirmed": "yes"},
            {},
        ):
            with pytest.raises(BridgeError) as refused:
                await bridge.setup(kind, str(uuid4()), payload)
            assert refused.value.code == "OWNER_CONFIRMATION_REQUIRED" and refused.value.status == 400
    # Nothing queued or journalled.
    assert [row["kind"] for row in service.journal.list_operations()] == ["acquire"]
    queued = await bridge.setup("pair", str(uuid4()), {"udid": "simulated-phone", "owner_confirmed": True})
    assert queued["state"] == "QUEUED"
    record = await wait_for(bridge, queued["id"])
    assert record["state"] == "SUCCEEDED"
    assert record["result"] == {
        "trusted": True,
        "wifi_pairing": True,
        "udid": "simulated-phone",
        "simulated": True,
    }
    queued = await bridge.setup(
        "developer_mode", str(uuid4()), {"udid": "simulated-phone", "owner_confirmed": True}
    )
    assert (await wait_for(bridge, queued["id"]))["result"]["developer_mode"] is True
    queued = await bridge.setup("mount_ddi", str(uuid4()), {})
    assert (await wait_for(bridge, queued["id"]))["result"]["mounted"] is True
    await bridge.close()


async def test_full_page_scrolls_joins_and_serves_images_to_its_owner(service):
    bridge = local(service)
    op = str(uuid4())
    data = await bridge.full_page(op, {"max_pages": 8, "settle": 0, "stitch": True})
    assert data["operation"]["state"] == "SUCCEEDED", data
    summary = data["operation"]["result"]
    # The simulator scrolls 1200 points of content: several pages, then the end.
    assert summary["stopped"] == "end" and 3 <= summary["page_count"] <= 8
    assert '"image":' not in json.dumps(summary) and all(page["matched"] for page in summary["pages"])
    # The join is not pixel perfect: sparse circles leave blank overlaps, the last flick is clamped.
    total = sum(page["shift"] for page in summary["pages"])
    assert service.phone.device.SCROLL_MAX - 40 <= total <= service.phone.device.SCROLL_MAX + 300
    assert summary["current"]["frame_id"] == service.phone.frame.frame_id
    assert len(data["pages"]) == summary["page_count"]
    assert base64.b64decode(data["pages"][0]["image"]).startswith(b"\xff\xd8")
    stitched = data["stitched"]
    assert stitched["width"] == summary["pages"][0]["metadata"]["width"] and "image" in stitched
    # The identical request replays the record without touching the phone.
    swipes = len([r for r in service.phone.device.reports if r[0] == "touch" and r[1][0] == "release"])
    replay = await bridge.full_page(op, {"max_pages": 8, "settle": 0, "stitch": True})
    assert replay["operation"]["id"] == op
    assert swipes == len(
        [r for r in service.phone.device.reports if r[0] == "touch" and r[1][0] == "release"]
    )
    # The last page is where the screen stays, so its frame is valid for the next touch.
    current = summary["current"]
    tap = {
        "kind": "tap",
        "x": 0.5,
        "y": 0.5,
        "frame_id": current["frame_id"],
        "geometry_epoch": current["geometry_epoch"],
    }
    assert (await bridge.action(str(uuid4()), tap, False))["operation"]["state"] == "SUCCEEDED"
    # Another caller cannot read the images.
    other = local(service)
    with pytest.raises(BridgeError) as hidden:
        await other._run(lambda: service.phone.full_page_result(op, other.owner))
    assert hidden.value.code == "OPERATION_NOT_FOUND"
    await bridge.close()
