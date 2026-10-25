import asyncio
import base64
import struct

import pytest
from support import raises_code

from thumbly.coredevice.hid import ReportGate
from thumbly.coredevice.worker import PhoneWorker
from thumbly.domain.errors import ThumblyError
from thumbly.domain.models import Binding, CaptureOptions, Frame, FrameMetadata
from thumbly.workers.protocol import rpc, serve


def test_report_generation_rejects_after_release_and_validates_reports():
    gate = ReportGate()
    old = gate.begin()
    gate.accept(old, "keyboard", [4])
    gate.release()
    with pytest.raises(ThumblyError):
        gate.accept(old, "keyboard", [])
    new = gate.begin()
    with pytest.raises(ThumblyError):
        gate.accept(old, "keyboard", [])
    gate.accept(new, "touch", ["contact", 65535, 0])
    for bad in (
        ("touch", ["hover", 1, 1]),
        ("touch", ["contact", 65536, 0]),
        ("keyboard", [0xE8]),
        ("keyboard", list(range(1, 10))),
        ("button", [0x0C, 0x40, "hold"]),
        ("pointer", [0, 0, 0]),
    ):
        with raises_code("INVALID_REPORT"):
            gate.accept(new, *bad)
    gate.accept(new, "button", [0x0C, 0x40, "down"])
    gate.last_report = 0
    assert gate.expired()
    with raises_code("INPUT_EXPIRED"):
        gate.accept(new, "keyboard", [])


async def test_ipc_version_permissions_and_limits(tmp_path):
    path = tmp_path / "worker.sock"

    async def dispatch(request):
        return {"echo": request["op"]}

    task = asyncio.create_task(serve(path, dispatch))
    try:
        while not path.exists():
            await asyncio.sleep(0.001)
        assert path.stat().st_mode & 0o777 == 0o600
        assert await rpc(path, {"op": "status"}) == {"echo": "status"}
        with pytest.raises(ThumblyError):
            await rpc(path, {"version": 99, "op": "status"})
        reader, writer = await asyncio.open_unix_connection(str(path))
        writer.write(struct.pack("!I", 100000))
        await writer.drain()
        from thumbly.workers.protocol import read

        assert (await read(reader, 1000))["error"]["code"] == "IPC_TOO_LARGE"
        writer.close()
        await writer.wait_closed()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class FakeManager:
    def __init__(self):
        self.calls = []
        self.transports = {"usb": object(), "wifi": object()}

    async def start(self):
        self.calls.append("start")

    async def close(self):
        self.calls.append("close")

    async def connect(self, binding):
        self.calls.append(("connect", binding.transport))
        return {"state": "unavailable", "connection": {"state": "waiting_for_device"}}

    def status(self):
        return {"state": "ready", "connection": {"state": "connected"}}

    async def capture(self, binding, options):
        self.calls.append(("capture", options.format))
        metadata = FrameMetadata(
            geometry_epoch="g",
            width=1,
            height=2,
            native_width=1,
            native_height=2,
            content_rect=(0, 0, 1, 2),
            received_at=0,
            request_seconds=0,
            media_type="image/png",
        )
        return Frame(metadata, b"\x89PNG")

    async def release_all(self):
        self.calls.append("release")

    async def report(self, generation, kind, value):
        self.calls.append(("report", kind, value))

    async def find(self):
        self.calls.append("search")
        return {"state": "unavailable", "connection": {"state": "waiting_for_device", "searching": True}}

    async def usb_events(self, since, timeout):
        self.calls.append(("usb_events", since, timeout))
        return {"changes": 1, "serials": ["00008110-000A1B2C3D4E5F60"], "listening": True}

    async def gesture(self, generation, points):
        self.calls.append(("gesture", generation, len(points)))
        return {"reports": len(points), "median_interval_ms": 33, "max_gap_ms": 40}


async def test_worker_requires_persisted_binding_and_never_pairs_from_capture(service):
    manager = FakeManager()
    worker = PhoneWorker(service.config, manager)
    await worker.start()
    assert manager.calls == ["start", ("connect", "usb")]
    bound = service.phone.binding()
    other = bound.model_copy(update={"udid": "different-phone"})
    with pytest.raises(ThumblyError, match="persisted"):
        await worker.dispatch({"op": "capture", "binding": other.model_dump(), "options": {}})
    result = await worker.dispatch({"op": "capture", "binding": bound.model_dump(), "options": {}})
    assert base64.b64decode(result["image"]) == b"\x89PNG" and result["metadata"]["width"] == 1
    with raises_code("OWNER_CONFIRMATION_REQUIRED"):
        await worker.dispatch({"op": "pair", "udid": bound.udid})
    with raises_code("UNKNOWN_OPERATION"):
        await worker.dispatch({"op": "pair_window"})
    assert await worker.dispatch({"op": "release"}) == {"released": True}
    assert not any(isinstance(c, tuple) and c[0] == "pair" for c in manager.calls)


def test_binding_transport_is_explicit():
    binding = Binding(udid="00008110-000A1B2C3D4E5F60", owner_confirmed=True, portrait_locked=True)
    assert binding.transport == "auto"
    for invalid in (
        {"udid": "00008110-000A1B2C3D4E5F60", "transport": "serial"},
        {"udid": "00008110-000A1B2C3D4E5F60", "owner_confirmed": False},
        {"udid": "00008110-000A1B2C3D4E5F60", "usb_identity": "old-phone"},  # Pre-CoreDevice field.
    ):
        with pytest.raises(ValueError):
            Binding.model_validate({"owner_confirmed": True, "portrait_locked": True, **invalid})
    assert CaptureOptions().format == "jpeg"


async def test_client_dispatches_onboarding_ops_with_owner_confirmation(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from thumbly.adapters.device import worker_client
    from thumbly.adapters.device.worker_client import WorkerClient

    path = tmp_path / "device.sock"
    seen, timeouts = [], []

    async def dispatch(request):
        seen.append(request)
        return {"op": request["op"], "ok": True}

    async def spy(socket, command, timeout=3):
        timeouts.append(timeout)
        return await rpc(socket, command, timeout)

    monkeypatch.setattr(worker_client, "rpc", spy)
    task = asyncio.create_task(serve(path, dispatch))
    try:
        while not path.exists():
            await asyncio.sleep(0.001)
        client = WorkerClient(SimpleNamespace(runtime_dir=tmp_path, capture_deadline=1))
        assert client.socket == path
        udid = "00008110-000A1B2C3D4E5F60"
        assert await client.discover() == {"op": "discover", "ok": True}
        assert await client.pair(udid) == {"op": "pair", "ok": True}
        assert await client.developer_mode(udid) == {"op": "developer_mode", "ok": True}
        assert await client.mount_ddi() == {"op": "mount_ddi", "ok": True}
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert seen == [
        {"op": "discover"},
        {"op": "pair", "udid": udid, "owner_confirmed": True},
        {"op": "developer_mode", "udid": udid, "owner_confirmed": True},
        {"op": "mount_ddi"},
    ]
    assert timeouts == [30, 130, 40, 10]


async def test_worker_usb_events_ops(service):
    manager = FakeManager()
    worker = PhoneWorker(service.config, manager)
    cable = await worker.dispatch({"op": "usb_events", "since": 0, "timeout": 25})
    assert cable["serials"] and manager.calls[-1] == ("usb_events", 0, 25.0)
    await worker.dispatch({"op": "usb_events", "since": None})
    assert manager.calls[-1] == ("usb_events", None, 0.0)
    for bad in ({"since": "x", "timeout": 1}, {"since": 0, "timeout": 26}, {"since": 0, "timeout": -1}):
        with raises_code("INVALID_REQUEST"):
            await worker.dispatch({"op": "usb_events", **bad})
