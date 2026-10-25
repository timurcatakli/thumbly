import asyncio
from types import SimpleNamespace

from thumbly.coredevice import link_session
from thumbly.coredevice.link_session import LinkSession


class Recorder(LinkSession):
    """A session whose stream never yields a frame (a locked phone) and whose PNG capture is a stub."""

    def __init__(self):
        super().__init__(None, SimpleNamespace(hid_refresh_seconds=15), lambda: None)
        self.calls = []
        self.fake_hid = object()

    async def _stream_screenshot(self, binding, options, started):
        self.calls.append("stream")
        return None

    async def _ensure_stream_locked(self):
        self.calls.append("stream-open")

    async def _open_hid(self, service_class):
        return self.fake_hid

    async def stop_hid(self):
        self.calls.append("stop")

    async def _invoke(self, factory, call, timeout: float, name: str = "call", fatal: bool = True):
        self.calls.append("png")
        raise link_session.ThumblyError("CAPTURE_FAILED", "stub")


async def shoot(session):
    try:
        await session.screenshot(None, SimpleNamespace(), 5)
    except link_session.ThumblyError:
        pass


def test_a_stream_miss_sends_the_next_screenshots_straight_to_png(monkeypatch):
    session = Recorder()
    asyncio.run(shoot(session))
    assert session.calls == ["stream", "stop", "png"]
    session.calls.clear()
    asyncio.run(shoot(session))
    assert session.calls == ["png"]

    clock = session.stream_missed + link_session.STREAM_RETRY_SECONDS + 1
    monkeypatch.setattr(link_session.time, "monotonic", lambda: clock)
    session.calls.clear()
    asyncio.run(shoot(session))
    assert session.calls == ["stream", "stop", "png"]


def test_input_clears_the_miss():
    session = Recorder()
    asyncio.run(shoot(session))
    asyncio.run(session.ensure_hid())
    session.calls.clear()
    asyncio.run(shoot(session))
    assert session.calls[0] == "stream"


class FakeHID:
    def __init__(self):
        self.calls = []

    async def create_keyboard_service(self):
        self.calls.append("keyboard")
        return 7

    async def send_keyboard(self, service_id, usages):
        self.calls.append(("keys", service_id, list(usages)))

    async def send_touchscreen(self, state, x, y):
        self.calls.append("touch")

    async def close(self):
        self.calls.append("close")


def test_the_keyboard_attaches_only_while_typing():
    async def run():
        session = Recorder()
        hid = FakeHID()
        session.fake_hid = hid
        await session.ensure_hid()
        await session.touch("contact", 1, 2)
        await session.touch("release", 1, 2)
        await session.release_all()
        assert hid.calls == ["touch", "touch"] and session.hid is hid  # Taps leave no keyboard.

        await session.keyboard([4])
        await session.release_all()
        assert hid.calls[2:] == ["keyboard", ("keys", 7, [4]), ("keys", 7, []), "close"]
        assert session.hid is None and session.keyboard_id is None

    asyncio.run(run())
