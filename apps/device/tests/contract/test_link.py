"""CoreDeviceManager state machine with fake transports, tunnels and sessions."""

import asyncio
import contextlib
import time

import pytest
from support import raises_code

from thumbly.config import Config
from thumbly.coredevice import link as manager_module
from thumbly.coredevice.link import PhoneLink
from thumbly.coredevice.state import State
from thumbly.domain.errors import ThumblyError
from thumbly.domain.models import Binding, CaptureOptions, Frame, FrameMetadata

BINDING = Binding(
    udid="00008110-AAAAAAAAAAAAAAAA", transport="usb", owner_confirmed=True, portrait_locked=True
)


class FakeTransport:
    poll_seconds = 0.01

    def __init__(self, present=True, error=None):
        self.is_present = present
        self.error = error
        self.opened = 0
        self.attempts = 0
        self.verifications = []

    async def present(self, udid):
        return self.is_present

    async def open(self, udid):
        self.attempts += 1
        if self.error:
            raise self.error
        self.opened += 1
        return f"provider-{self.opened}", [], {"product_version": "27.0"}

    async def verify(self, udid):
        self.verifications.append(udid)
        return {"present": self.is_present, "trusted": True}


class FakeRsd:
    udid = BINDING.udid
    product_version = "27.0"


class FakeTunnel:
    instances = []

    def __init__(self, provider, closers, mode="userspace"):
        self.provider = provider
        self.mode = mode
        self.closed = asyncio.Event()
        self.open_count = 0
        FakeTunnel.instances.append(self)

    async def open(self):
        self.open_count += 1
        return FakeRsd()

    async def wait_closed(self):
        await self.closed.wait()

    async def aclose(self):
        self.closed.set()


class FakeSession:
    prepare_results = []
    instances = []

    def __init__(self, rsd, config, on_lost):
        self.on_lost = on_lost
        self.events = []
        self.hid_active = False
        self.closed = False
        self.probes = 0
        self.idle_checks = 0
        FakeSession.instances.append(self)

    async def prepare(self, auto_mount, on_state):
        if FakeSession.prepare_results:
            result = FakeSession.prepare_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return {"developer_mode": True, "ddi_mounted": True, "mounted_now": False}

    async def open(self):
        pass

    async def probe(self):
        self.probes += 1

    async def idle_check(self):
        self.idle_checks += 1
        self.hid_active = False

    async def screenshot(self, binding, options, deadline):
        metadata = FrameMetadata(
            geometry_epoch="g",
            width=390,
            height=844,
            native_width=390,
            native_height=844,
            content_rect=(0, 0, 390, 844),
            received_at=0,
            request_seconds=0,
            media_type="image/png",
        )
        return Frame(metadata, b"\x89PNG")

    async def ensure_hid(self):
        self.hid_active = True

    async def ensure_buttons(self):
        pass

    async def touch(self, state, x, y):
        self.events.append(("touch", state, x, y))

    async def keyboard(self, usages):
        self.events.append(("keyboard", list(usages)))

    async def button(self, page, usage, state):
        self.events.append(("button", page, usage, state))

    async def release_all(self):
        self.events.append("release")

    async def close(self):
        self.closed = True

    async def list_apps(self):
        return [{"bundle_id": "com.apple.Preferences", "name": "Settings"}]

    async def device_info(self):
        return {"product_type": "iPhone14,5"}


class Clock:
    """Monotonic time plus a jump offset: fake browses and the 30-minute idle advance it."""

    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return time.monotonic() + self.offset

    def advance(self, seconds):
        self.offset += seconds


class FakeUsb(FakeTransport):
    """USB with usbmuxd-style events: ``plug`` changes presence and pushes an attach/detach."""

    def __init__(self, present=True, error=None, listen=True):
        super().__init__(present, error)
        self.listen = listen
        self.events: asyncio.Queue = asyncio.Queue()
        self.checks = 0
        self.polls = 0
        self.watching = 0

    async def present(self, udid):
        self.checks += 1
        return self.is_present

    async def serials(self):
        self.polls += 1
        return {BINDING.udid} if self.is_present else set()

    async def watch(self):
        if not self.listen:
            raise ConnectionError("Listen refused")
        self.watching += 1
        try:
            yield await self.serials()
            while True:
                yield await self.events.get()
        finally:
            self.watching -= 1

    def plug(self, present=True):
        self.is_present = present
        self.events.put_nowait({BINDING.udid} if present else set())


class FakeWifi(FakeTransport):
    """Bonjour browse that takes ``seconds`` of fake time; ``hang`` blocks it until cancelled."""

    def __init__(self, clock, present=False, paired=True, seconds=3.0):
        super().__init__(present)
        self.clock, self.seconds = clock, seconds
        self.is_paired = paired
        self.browses = 0
        self.hang = False
        self.in_flight = 0
        self.max_in_flight = 0
        self.forgotten = 0
        self.last = {}

    def paired(self, udid):
        return self.is_paired

    def matches(self, udid, properties):
        return properties.get("authTag") == "ok"

    def remember(self, udid, host, port):
        self.last[udid] = (host, port)

    def forget(self, udid=None):
        self.forgotten += 1
        self.forgotten_udid = udid

    async def present(self, udid):
        self.browses += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.hang:
                await asyncio.Event().wait()
            await asyncio.sleep(0)
            self.clock.advance(self.seconds)
            return self.is_present
        finally:
            self.in_flight -= 1


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def manager(tmp_path, clock):
    FakeTunnel.instances.clear()
    FakeSession.instances.clear()
    FakeSession.prepare_results.clear()
    config = Config(
        backend="simulated",
        state_dir=tmp_path,
        runtime_dir=tmp_path,
        probe_interval=1,
        reconnect_min_seconds=0.01,
        reconnect_max_seconds=0.05,
        recover_wait_seconds=1,
    )
    transports = {"usb": FakeUsb(), "wifi": FakeWifi(clock)}
    return PhoneLink(config, transports, FakeTunnel, FakeSession, clock=clock)


async def wait_state(manager, state, timeout=2):
    async with asyncio.timeout(timeout):
        while manager.connection.state != state.value:
            await asyncio.sleep(0.005)


async def settle(manager, rounds=50):
    """Let every pending callback run; the worker has no timers when idle, so this is enough."""
    for _ in range(rounds):
        await asyncio.sleep(0)
    await asyncio.sleep(0.02)


async def test_connect_capture_input_and_transport_loss_recovery(manager):
    await manager.start()
    try:
        with raises_code("DEVICE_DISCONNECTED"):
            await manager.capture(BINDING, CaptureOptions())
        await manager.connect(BINDING)
        await wait_state(manager, State.CONNECTED)
        status = manager.status()
        assert status["state"] == "ready" and status["connection"]["transport"] == "usb"
        assert status["connection"]["product_version"] == "27.0"
        frame = await manager.capture(BINDING, CaptureOptions())
        assert frame.image == b"\x89PNG"
        generation = await manager.begin(BINDING)
        await manager.report(generation, "touch", ["contact", 100, 200])
        await manager.report(generation, "keyboard", [4])
        await manager.report(generation, "button", [12, 64, "down"])
        session = FakeSession.instances[-1]
        assert session.events[-3:] == [
            ("touch", "contact", 100, 200),
            ("keyboard", [4]),
            ("button", 12, 64, "down"),
        ]
        await manager.release_all()
        assert session.events[-1] == "release" and manager.gate.generation is None
        with raises_code("INPUT_INTERRUPTED"):
            await manager.report(generation, "keyboard", [])
        # USB unplug: the tunnel transport closes, the supervisor waits and then reconnects.
        manager.transports["usb"].plug(False)
        FakeTunnel.instances[-1].closed.set()
        await wait_state(manager, State.WAITING_FOR_DEVICE)
        assert session.closed
        with raises_code("DEVICE_DISCONNECTED"):
            await manager.capture(BINDING, CaptureOptions())
        manager.transports["usb"].plug(True)
        await wait_state(manager, State.CONNECTED)
        assert manager.transports["usb"].opened == 2 and manager.connection.attempts == 0
        assert (await manager.list_apps(BINDING))[0]["bundle_id"] == "com.apple.Preferences"
        assert (await manager.device_info(BINDING))["transport"] == "usb"
    finally:
        await manager.close()
    assert manager.connection.state == State.DISCONNECTED.value


async def test_owner_action_states_and_identity(manager):
    manager.transports["usb"].error = ThumblyError("TRUST_REQUIRED", "pair first")
    await manager.start()
    try:
        await manager.connect(BINDING)
        await wait_state(manager, State.TRUST_REQUIRED)
        assert manager.connection.last_error["code"] == "TRUST_REQUIRED"
        with pytest.raises(ThumblyError, match="owner pairing"):
            await manager.capture(BINDING, CaptureOptions())
        manager.transports["usb"].error = None
        FakeSession.prepare_results.append(ThumblyError("DEVELOPER_MODE_REQUIRED"))
        manager.wake.set()
        manager._pulse()
        await wait_state(manager, State.DEVELOPER_MODE_REQUIRED)
        manager.wake.set()
        manager._pulse()
        await wait_state(manager, State.CONNECTED)
        other = BINDING.model_copy(update={"udid": "00008110-BBBBBBBBBBBBBBBB"})
        with raises_code("IDENTITY_MISMATCH"):
            await manager.capture(other, CaptureOptions())
        wifi = BINDING.model_copy(update={"transport": "wifi"})
        with raises_code("DEVICE_DISCONNECTED"):
            await manager.verify(wifi)
        assert (await manager.verify(BINDING))["transport"] == "usb"
    finally:
        await manager.close()


async def test_ddi_mount_refreshes_services_and_recover_forces_reconnect(manager):
    FakeSession.prepare_results.append({"developer_mode": True, "ddi_mounted": True, "mounted_now": True})
    await manager.start()
    try:
        await manager.connect(BINDING)
        await wait_state(manager, State.CONNECTED)
        assert manager.transports["usb"].opened == 2  # Remount after the DDI is mounted, no backoff.
        assert manager.connection.ddi_mounted is True
        before = manager.transports["usb"].opened
        result = await manager.recover(BINDING)
        assert result["state"] == "ready" and manager.transports["usb"].opened == before + 1
    finally:
        await manager.close()


async def test_watchdog_releases_after_api_death(manager):
    await manager.start()
    try:
        await manager.connect(BINDING)
        await wait_state(manager, State.CONNECTED)
        await manager.begin(BINDING)
        manager.gate.last_report -= 10
        async with asyncio.timeout(2):
            while manager.gate.generation is not None:
                await asyncio.sleep(0.01)
        assert FakeSession.instances[-1].events[-1] == "release"
    finally:
        await manager.close()


async def test_switching_transport_restarts_supervision(manager):
    await manager.start()
    try:
        await manager.connect(BINDING)
        await wait_state(manager, State.CONNECTED)
        wifi = BINDING.model_copy(update={"transport": "wifi"})
        await manager.connect(wifi)
        await wait_state(manager, State.WAITING_FOR_DEVICE)
        assert manager.connection.transport == "wifi"
        with raises_code("IDENTITY_MISMATCH"):
            await manager.capture(BINDING, CaptureOptions())
        manager.transports["wifi"].is_present = True
        await manager.find()  # An explicit search opens a window.
        await wait_state(manager, State.CONNECTED)
        assert manager.transports["wifi"].opened == 1
    finally:
        with contextlib.suppress(Exception):
            await manager.close()


AUTO = BINDING.model_copy(update={"transport": "auto"})


async def wait_transport(manager, transport, timeout=2):
    async with asyncio.timeout(timeout):
        while (manager.connection.transport, manager.connection.state) != (transport, State.CONNECTED.value):
            await asyncio.sleep(0.005)


async def test_auto_moves_to_wifi_on_unplug_and_back_to_usb_when_idle(manager):
    usb, wifi = manager.transports["usb"], manager.transports["wifi"]
    await manager.start()
    try:
        await manager.connect(AUTO)
        await wait_state(manager, State.CONNECTED)
        assert manager.connection.transport == "usb" and usb.opened == 1
        # Cable pulled while the phone is on the LOCAL: the next attempt finds it over Wi-Fi.
        wifi.is_present = True
        usb.plug(False)
        FakeTunnel.instances[-1].closed.set()
        await wait_transport(manager, "wifi")
        assert wifi.opened == 1
        assert (await manager.device_info(AUTO))["transport"] == "wifi"
        # Cable back while an input generation is open: the move waits for the idle moment.
        generation = await manager.begin(AUTO)
        usb.plug(True)
        await asyncio.sleep(0.15)
        assert manager.connection.transport == "wifi" and usb.opened == 1
        await manager.report(generation, "touch", ["contact", 1, 1])
        await manager.release_all()
        await wait_transport(manager, "usb")
        assert usb.opened == 2 and manager.connection.attempts == 0
        assert manager.connection.last_error is None
        assert FakeSession.instances[-2].closed
    finally:
        await manager.close()


async def test_auto_requests_wait_through_a_switch(manager):
    usb, wifi = manager.transports["usb"], manager.transports["wifi"]
    wifi.is_present = True
    manager.config.switch_hold_seconds = 2
    await manager.start()
    try:
        await manager.connect(AUTO)
        await wait_state(manager, State.CONNECTED)
        usb.plug(False)
        FakeTunnel.instances[-1].closed.set()
        await wait_state(manager, State.RECONNECTING)
        # Issued right after the loss: held until the Wi-Fi session is up, then served.
        frame = await manager.capture(AUTO, CaptureOptions())
        assert frame.image == b"\x89PNG" and manager.connection.transport == "wifi"
        # Nowhere to go: the wait ends once the supervisor is waiting for the phone.
        wifi.is_present = False
        FakeTunnel.instances[-1].closed.set()
        await wait_state(manager, State.WAITING_FOR_DEVICE)
        assert manager.connection.transport is None and manager.connection.wifi_paired is True
        with raises_code("DEVICE_DISCONNECTED"):
            await manager.capture(AUTO, CaptureOptions())
    finally:
        await manager.close()


async def test_auto_skips_unpaired_wifi_and_reports_it(manager):
    usb, wifi = manager.transports["usb"], manager.transports["wifi"]
    wifi.is_paired = False
    usb.is_present = False
    await manager.start()
    try:
        await manager.connect(AUTO)
        await wait_state(manager, State.WAITING_FOR_DEVICE)
        await asyncio.sleep(0.05)
        assert manager.connection.wifi_paired is False and wifi.opened == 0
        with raises_code("DEVICE_DISCONNECTED"):
            await manager.verify(AUTO)
        usb.plug(True)
        assert (await manager.verify(AUTO))["transport"] == "usb"
        await wait_state(manager, State.CONNECTED)
        assert manager.connection.transport == "usb"
        # Unbind forgets the bound phone's records only and leaves nothing bound.
        result = await manager.unbind()
        assert result["connection"]["state"] == "disconnected" and manager.binding is None
        assert wifi.forgotten == 1 and wifi.forgotten_udid == AUTO.udid
    finally:
        await manager.close()


async def test_auto_falls_back_to_wifi_when_the_usb_session_fails(manager):
    usb, wifi = manager.transports["usb"], manager.transports["wifi"]
    wifi.is_present = True
    usb.error = ThumblyError("TRUST_REQUIRED", "pair first")
    await manager.start()
    try:
        await manager.connect(AUTO)
        await wait_transport(manager, "wifi")
        assert usb.opened == 0 and wifi.opened == 1 and manager.connection.last_error is None
        # The cable is still there, but a failed USB path must not flap the Wi-Fi session.
        await asyncio.sleep(0.2)
        assert manager.connection.transport == "wifi" and wifi.opened == 1
        usb.error = None
        await asyncio.sleep(0.2)
        assert manager.connection.transport == "wifi" and wifi.opened == 1
        # Only once the phone has been off the cable is the move to USB attempted again.
        usb.plug(False)
        await asyncio.sleep(0.05)
        usb.plug(True)
        await wait_transport(manager, "usb")
        assert usb.opened == 1
    finally:
        await manager.close()


# ----- search windows (no background discovery) ------------------------------------------


async def boot_without_phone(manager, transport="auto"):
    manager.transports["usb"].is_present = False
    await manager.start()
    await manager.connect(BINDING.model_copy(update={"transport": transport}))
    await wait_state(manager, State.WAITING_FOR_DEVICE)
    async with asyncio.timeout(2):
        while manager.window is None or not manager.window.done():
            await asyncio.sleep(0.005)
    await settle(manager)


def window_browses(manager):
    """Browses one window may run: never one that ends more than 1 s past the deadline."""
    return int((manager.config.search_window_seconds + 1) // manager.config.discovery_seconds)


async def test_no_phone_for_30_minutes_is_one_boot_window_then_idle(manager, clock):
    usb, wifi = manager.transports["usb"], manager.transports["wifi"]
    try:
        await boot_without_phone(manager)
        assert manager.windows == 1 and 1 <= wifi.browses <= window_browses(manager)
        assert manager.connection.searching is False
        assert manager.status()["connection"]["searched_at"] > 0
        assert manager.status()["connection"]["message"] == "Unlock the iPhone or plug it into this Mac"
        browses, checks = wifi.browses, usb.checks
        clock.advance(30 * 60)
        await settle(manager, 200)
        assert manager.windows == 1 and wifi.browses == browses and usb.checks == checks
        assert usb.polls == 1 and usb.watching == 1  # One Listen, no presence polling.
    finally:
        await manager.close()
    assert usb.watching == 0  # Teardown closes the Listen connection.


async def test_search_coalesces_and_is_bounded(manager):
    wifi = manager.transports["wifi"]
    try:
        await boot_without_phone(manager)
        before = wifi.browses
        wifi.hang = True
        windows = [manager.search() for _ in range(5)]
        assert manager.windows == 2 and all(w is windows[0] for w in windows)
        await settle(manager)
        assert wifi.max_in_flight == 1
        wifi.hang = False
        windows[0].cancel()
        await settle(manager)
        manager.search()
        await manager._await_window(manager.window)
        assert manager.windows == 3 and wifi.browses - before <= 1 + window_browses(manager)
        await settle(manager)
        assert manager.connection.state == State.WAITING_FOR_DEVICE.value
    finally:
        await manager.close()


async def test_requests_while_disconnected_share_one_window_then_fail_with_hint(manager):
    wifi = manager.transports["wifi"]
    try:
        await boot_without_phone(manager)
        results = await asyncio.gather(
            manager.capture(AUTO, CaptureOptions()),
            manager.list_apps(AUTO),
            return_exceptions=True,
        )
        assert manager.windows == 2
        for result in results:
            assert isinstance(result, ThumblyError) and result.code == "DEVICE_DISCONNECTED"
            assert result.message == "Unlock the iPhone or plug it into this Mac"
        browses = wifi.browses
        await settle(manager, 100)
        assert wifi.browses == browses and manager.windows == 2
    finally:
        await manager.close()


async def test_request_finds_the_phone_inside_its_window(manager):
    wifi = manager.transports["wifi"]
    try:
        await boot_without_phone(manager)
        wifi.is_present = True
        frame = await manager.capture(AUTO, CaptureOptions())
        assert frame.image == b"\x89PNG" and manager.connection.transport == "wifi"
        assert manager.windows == 2
    finally:
        await manager.close()


async def test_attach_connects_over_usb_without_browsing(manager):
    usb, wifi = manager.transports["usb"], manager.transports["wifi"]
    try:
        await boot_without_phone(manager)
        browses, windows = wifi.browses, manager.windows
        usb.plug(True)
        await wait_transport(manager, "usb")
        assert wifi.browses == browses and manager.windows == windows
    finally:
        await manager.close()


async def test_detach_opens_one_window_then_idles(manager):
    usb, wifi = manager.transports["usb"], manager.transports["wifi"]
    await manager.start()
    try:
        await manager.connect(AUTO)
        await wait_transport(manager, "usb")
        assert manager.windows == 0 and wifi.browses == 0  # The cable was found first: no browse.
        usb.plug(False)
        FakeTunnel.instances[-1].closed.set()
        await wait_state(manager, State.WAITING_FOR_DEVICE)
        await manager._await_window(manager.window)
        await settle(manager, 100)
        assert manager.windows == 1 and 1 <= wifi.browses <= window_browses(manager)
        browses = wifi.browses
        await settle(manager, 100)
        assert wifi.browses == browses and manager.connection.state == State.WAITING_FOR_DEVICE.value
    finally:
        await manager.close()


async def test_wifi_session_loss_opens_one_window_then_idles(manager):
    usb, wifi = manager.transports["usb"], manager.transports["wifi"]
    try:
        await boot_without_phone(manager)
        wifi.is_present = True
        await manager.find()
        await wait_transport(manager, "wifi")
        windows, browses = manager.windows, wifi.browses
        wifi.is_present = False
        FakeTunnel.instances[-1].closed.set()
        await wait_state(manager, State.WAITING_FOR_DEVICE)
        await manager._await_window(manager.window)
        await settle(manager, 100)
        assert manager.windows == windows + 1
        assert 1 <= wifi.browses - browses <= window_browses(manager)
        browses = wifi.browses
        await settle(manager, 100)
        assert wifi.browses == browses and usb.polls == 1
    finally:
        await manager.close()


async def test_listen_failure_polls_presence_only_and_never_browses(manager):
    usb, wifi = manager.transports["usb"], manager.transports["wifi"]
    usb.listen = False
    usb.is_present = False
    await manager.start()
    try:
        await manager.connect(BINDING)  # USB binding: windows check the cable, never browse.
        await asyncio.sleep(0.1)
        assert usb.polls >= 2 and wifi.browses == 0 and not manager.listening
        usb.is_present = True  # No event: the fallback poll sees the attach.
        await wait_state(manager, State.CONNECTED)
        assert wifi.browses == 0
    finally:
        await manager.close()


async def test_cancellation_during_a_browse_leaves_no_task(manager):
    wifi = manager.transports["wifi"]
    wifi.hang = True
    before = set(asyncio.all_tasks())
    manager.transports["usb"].is_present = False
    await manager.start()
    await manager.connect(AUTO)
    async with asyncio.timeout(2):
        while wifi.in_flight == 0:
            await asyncio.sleep(0.005)
    await manager.close()
    await settle(manager)
    assert wifi.in_flight == 0 and manager.window is None
    assert set(asyncio.all_tasks()) - before == set()


async def test_owner_state_waits_for_an_event_not_a_timer(manager):
    usb = manager.transports["usb"]
    usb.error = ThumblyError("TRUST_REQUIRED", "pair first")
    await manager.start()
    try:
        await manager.connect(BINDING)
        await wait_state(manager, State.TRUST_REQUIRED)
        await settle(manager, 100)
        attempts = manager.connection.attempts
        await asyncio.sleep(0.2)
        assert manager.connection.attempts == attempts  # No polling retry.
        usb.error = None
        await manager.find()  # A search after tapping Trust.
        await wait_state(manager, State.CONNECTED)
    finally:
        await manager.close()


# ----- probes, safety-net windows, advertisements, locked phone ---------------------------


class LockedError(Exception):
    """pymobiledevice3's refusal from a phone locked for more than ~30 s."""


async def test_usb_is_never_probed_and_wifi_only_after_an_idle_interval(manager):
    await manager.start()
    try:
        await manager.connect(BINDING)
        await wait_state(manager, State.CONNECTED)
        await asyncio.sleep(1.3)  # probe_interval is 1 s.
        assert FakeSession.instances[-1].probes == 0
    finally:
        await manager.close()
    wifi = manager.transports["wifi"]
    wifi.is_present = True
    await manager.start()
    try:
        await manager.connect(BINDING.model_copy(update={"transport": "wifi"}))
        await wait_state(manager, State.CONNECTED)
        session = FakeSession.instances[-1]
        for _ in range(4):  # A request every 0.4 s keeps the probe away.
            await asyncio.sleep(0.4)
            await manager.list_apps(BINDING.model_copy(update={"transport": "wifi"}))
        assert session.probes == 0
        await asyncio.sleep(1.3)
        assert session.probes == 1
    finally:
        await manager.close()


async def test_input_session_closes_on_its_own_timer_without_a_probe(manager):
    manager.config = manager.config.model_copy(update={"hid_idle_seconds": 1})
    await manager.start()
    try:
        await manager.connect(BINDING)
        await wait_state(manager, State.CONNECTED)
        session = FakeSession.instances[-1]
        await asyncio.sleep(0.2)
        assert session.idle_checks == 0  # Nothing open: no timer.
        generation = await manager.begin(BINDING)
        manager.gate.release()
        assert generation and session.hid_active
        async with asyncio.timeout(3):
            while session.hid_active:
                await asyncio.sleep(0.01)
        assert session.idle_checks == 1 and session.probes == 0 and not manager.connection.hid_active
    finally:
        await manager.close()


async def test_a_locked_phone_is_tried_once_and_requests_fail_fast(manager, clock):
    usb, wifi = manager.transports["usb"], manager.transports["wifi"]
    usb.is_present = False
    wifi.is_present = True
    wifi.error = LockedError("The device has not been unlocked recently")
    await manager.start()
    try:
        await manager.connect(AUTO)
        await wait_state(manager, State.PHONE_LOCKED)
        await settle(manager, 100)
        assert wifi.attempts == 1  # No immediate retry on the other path: it is locked there too.
        assert manager.status()["connection"]["message"].startswith("Unlock the phone")
        with raises_code("PHONE_LOCKED"):
            await manager.capture(AUTO, CaptureOptions())
        assert wifi.attempts == 2  # The request tried once more, in case it was just unlocked.
        wifi.error = None
        await manager.find()  # Unlocked; a search (or its advertisement) connects.
        await wait_transport(manager, "wifi")
    finally:
        await manager.close()


async def locked_phone(manager, monkeypatch):
    monkeypatch.setattr(manager_module, "LOCKED_RETRY_SECONDS", 0.02)
    usb, wifi = manager.transports["usb"], manager.transports["wifi"]
    usb.is_present = False
    wifi.is_present = True
    wifi.error = LockedError("The device has not been unlocked recently")
    await manager.start()
    await manager.connect(AUTO)
    await wait_state(manager, State.PHONE_LOCKED)
    async with asyncio.timeout(2):
        while wifi.attempts < 3:
            await asyncio.sleep(0.005)
    return wifi


async def test_press_then_unlock_connects_inside_the_window(manager, monkeypatch):
    wifi = await locked_phone(manager, monkeypatch)
    try:
        assert manager.windows == 1 and wifi.browses == 1  # Retried on the found address.
        assert manager.status()["connection"]["message"].startswith("Unlock the phone")
        wifi.error = None  # Unlocked: no second press needed.
        await wait_transport(manager, "wifi")
        assert manager.windows == 1
    finally:
        await manager.close()


async def test_locked_retries_end_with_the_window_and_a_press_starts_them_again(manager, clock, monkeypatch):
    wifi = await locked_phone(manager, monkeypatch)
    try:
        clock.advance(manager.config.search_window_seconds)
        await asyncio.sleep(0.1)
        attempts = wifi.attempts
        await asyncio.sleep(0.1)
        assert wifi.attempts == attempts and manager.connection.state == State.PHONE_LOCKED.value
        assert manager.connection.searching is False
        await manager.find()  # A search: a new window, and retries until it ends.
        async with asyncio.timeout(2):
            while wifi.attempts < attempts + 3:
                await asyncio.sleep(0.005)
        assert manager.windows == 2
        wifi.error = None
        await wait_transport(manager, "wifi")
    finally:
        await manager.close()


async def test_no_timer_searches_by_default(manager, clock):
    try:
        await boot_without_phone(manager)
        windows = manager.windows
        clock.advance(30 * 60)
        await asyncio.sleep(0.3)  # Longer than the 0.05-0.2 s delays that do fire in the safety-net test.
        await settle(manager, 200)
        assert manager.windows == windows  # Only events open windows: start, cable, request.
    finally:
        await manager.close()
