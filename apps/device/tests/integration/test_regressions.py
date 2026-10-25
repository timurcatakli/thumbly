import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError

from thumbly.application.shortcuts import validate_shortcut
from thumbly.domain.errors import ThumblyError
from thumbly.domain.models import SEND_TEXT_BYTES, Action, CaptureOptions, LinkRequest


async def test_duplicate_pointer_returns_record_after_new_frame(service, lease):
    frame = await service.phone.screenshot()
    op = str(uuid4())
    action = Action(
        kind="tap",
        x=0.25,
        y=0.75,
        frame_id=frame.metadata.frame_id,
        geometry_epoch=frame.metadata.geometry_epoch,
    )
    service.phone.action(op, "owner", lease, action)
    await service.phone.screenshot()
    result = service.phone.action(op, "owner", lease, action)
    assert result["state"] == "SUCCEEDED"
    presses = [v for k, v in service.phone.device.reports if k == "touch" and v[0] == "contact"]
    assert len(presses) == 1


async def test_capture_resize_and_jpeg_preserve_ratio(service):
    full = await service.phone.screenshot()
    small = await service.phone.screenshot(CaptureOptions(max_size=400, format="jpeg"))
    assert (small.metadata.width, small.metadata.height) == (185, 400)
    assert small.image.startswith(b"\xff\xd8")
    assert full.metadata.geometry_epoch == small.metadata.geometry_epoch
    assert full.metadata.stream_epoch != small.metadata.stream_epoch


async def test_expired_lease_cannot_replay_or_resume(service, lease):
    service.coordinator.lease.expires = 0
    with pytest.raises(ThumblyError):
        service.phone.resume("owner", lease)
    with pytest.raises(ThumblyError):
        service.phone.action(str(uuid4()), "owner", lease, Action(kind="key", key="enter"))
    new = (await service.phone.acquire_control("owner"))["lease_token"]
    assert new != lease


async def test_cancel_does_not_cancel_next_observation(service, lease):
    op = str(uuid4())
    service.phone.action(op, "owner", lease, Action(kind="type_text", text="abcdefghi"))
    await asyncio.sleep(0.02)
    observation = asyncio.create_task(service.phone.screenshot())
    await service.phone.cancel(op, "owner")
    assert (await observation).image
    assert service.phone.operation_status(op, "owner")["state"] == "OUTCOME_UNKNOWN"


async def test_single_api_process_lock(service):
    from thumbly.service import Service

    another = Service(service.config)
    try:
        with pytest.raises(RuntimeError, match="already running"):
            await another.start()
    finally:
        another.journal.close()


async def test_shortcuts_disabled_until_device_validation(service, lease):
    assert not service.phone.capabilities()["shortcuts"]
    with pytest.raises(ThumblyError):
        service.phone.shortcut(str(uuid4()), "owner", lease, "home")
    validate_shortcut(
        service.journal,
        service.phone.binding(),
        {
            "name": "home",
            "owner_confirmed": True,
            "evidence": "Synthetic test: home screen observed",
            "prerequisites": "U.S. keyboard; simulator only",
        },
    )
    assert service.phone.capabilities()["shortcuts"] == ["home"]


async def test_recovery_reports_failure_without_replaying_input(service):
    from thumbly.application.recovery import recover

    async def fail(binding):
        raise ThumblyError("DEVICE_DISCONNECTED")

    original = service.phone.device.recover
    service.phone.device.recover = fail
    result = await recover({"device": service.phone.device}, service.phone.binding())
    assert result["device"]["error"]["code"] == "DEVICE_DISCONNECTED"
    service.phone.device.recover = original
    before = service.phone.device.releases
    result = await recover({"device": service.phone.device}, service.phone.binding())
    assert result["device"]["state"] == "ready" and service.phone.device.releases > before


async def settle(service, op):
    async with asyncio.timeout(3):
        while service.phone.operation_status(op, "owner")["state"] in {"QUEUED", "DISPATCHED"}:
            await asyncio.sleep(0.005)
    return service.phone.operation_status(op, "owner")


async def test_transport_switch_rebinds_and_invalidates_frame(service, lease):
    await service.phone.screenshot()
    op = str(uuid4())
    service.phone.setup(op, "owner", lease, "transport", {"transport": "wifi"})
    record = await settle(service, op)
    assert record["state"] == "SUCCEEDED" and record["result"]["bound"]
    assert service.phone.binding().transport == "wifi" and service.phone.frame is None
    status = await service.phone.status()
    assert status["transport"] == "wifi" and status["connection"]["transport"] == "wifi"
    bad = str(uuid4())
    service.phone.setup(bad, "owner", lease, "transport", {"transport": "serial"})
    assert (await settle(service, bad))["result"]["code"] == "INVALID_REQUEST"


async def test_send_text_is_bounded_in_bytes_before_anything_is_recorded(service, lease):
    # 10,000 characters is the advertised limit, but two-byte characters pass it at 20 KB.
    text = "é" * (SEND_TEXT_BYTES // 2 + 1)
    op = str(uuid4())
    with pytest.raises(ThumblyError) as error:
        service.phone.action(op, "owner", lease, Action(kind="send_text", text=text))
    assert error.value.code == "REQUEST_TOO_LARGE" and error.value.status == 413
    with pytest.raises(ThumblyError, match="operation not found"):
        service.journal.get(op, "owner")
    fits = service.phone.action(str(uuid4()), "owner", lease, Action(kind="send_text", text="é" * 8000))
    assert fits["state"] in {"QUEUED", "DISPATCHED", "SUCCEEDED"}


def test_link_params_are_bounded():
    base = {"operation_id": str(uuid4()), "bundle_id": "net.whatsapp.WhatsApp", "link": "chat"}
    LinkRequest.model_validate({**base, "params": {f"p{i}": "x" * 2048 for i in range(8)}})
    for params in (
        {f"p{i}": "x" for i in range(9)},
        {"p": "x" * 2049},
        {"k" * 41: "x"},
    ):
        with pytest.raises(ValidationError):
            LinkRequest.model_validate({**base, "params": params})


async def test_another_screenshot_keeps_a_frame_valid_until_input(service):
    from thumbly.domain.models import Action

    first = (await service.phone.screenshot()).metadata
    await service.phone.screenshot()  # Another client looks at the screen.
    tap = Action(kind="tap", x=0.5, y=0.5, frame_id=first.frame_id, geometry_epoch=first.geometry_epoch)
    service.phone.validate_frame(tap)  # Still valid: nothing was pressed.
    service.phone._forget_frames()  # What any input does.
    with pytest.raises(ThumblyError, match="screenshot"):
        service.phone.validate_frame(tap)
