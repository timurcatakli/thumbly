"""Transports never pair implicitly and only ever address the explicitly bound phone."""

import asyncio
import sys
import types

import pytest
from support import raises_code

from thumbly.coredevice.state import classify, transport_lost
from thumbly.coredevice.transports import UsbTransport, WifiTransport, same_udid


def install(monkeypatch, **modules):
    monkeypatch.setitem(sys.modules, "pymobiledevice3", types.ModuleType("pymobiledevice3"))
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)


async def test_usb_transport_never_autopairs_and_reports_trust(monkeypatch, tmp_path):
    calls = []

    class Lockdown:
        paired = False
        product_version = "27.0"

        async def close(self):
            calls.append("closed")

    async def factory(**kwargs):
        calls.append(kwargs)
        return Lockdown()

    class Device:
        serial = "00008110-000A1B2C3D4E5F60"
        connection_type = "USB"
        is_usb = True

    async def list_devices():
        return [Device()]

    install(
        monkeypatch,
        **{
            "pymobiledevice3.lockdown": types.SimpleNamespace(create_using_usbmux=factory),
            "pymobiledevice3.usbmux": types.SimpleNamespace(list_devices=list_devices),
            "pymobiledevice3.exceptions": types.SimpleNamespace(
                InvalidServiceError=type("E", (Exception,), {})
            ),
            "pymobiledevice3.remote": types.ModuleType("pymobiledevice3.remote"),
            "pymobiledevice3.remote.tunnel_service": types.SimpleNamespace(CoreDeviceTunnelProxy=None),
        },
    )
    usb = UsbTransport(tmp_path / "lockdown")
    assert await usb.present("00008110000A1B2C3D4E5F60") and not await usb.present("other")
    with raises_code("TRUST_REQUIRED"):
        await usb.open(Device.serial)
    assert calls[0]["autopair"] is False and calls[0]["serial"] == Device.serial
    assert (
        calls[0]["connection_type"] == "USB"
        and calls[0]["pairing_records_cache_folder"] == tmp_path / "lockdown"
    )
    assert calls[-1] == "closed"
    assert (await usb.verify(Device.serial)) == {"present": True, "trusted": False}
    assert all(c["autopair"] is False for c in calls if isinstance(c, dict))


BOUND = "00008110-AAAAAAAAAAAAAAAA"


def answer(tag="ok", ip="192.168.4.71", port=49152):
    return types.SimpleNamespace(
        properties={"identifier": "svc", "authTag": tag},
        addresses=[
            types.SimpleNamespace(ip="fe80::1", full_ip="fe80::1%wlan0"),
            types.SimpleNamespace(ip=ip, full_ip=ip),
        ],
        port=port,
    )


class LockedError(Exception):
    """pymobiledevice3's refusal from a phone that has been locked for more than ~30 s."""


def wifi_fakes(monkeypatch, browses, dial):
    """Fake pymobiledevice3 for WifiTransport: ``browses`` is the answer list per sub-browse,
    ``dial(host, port)`` stands in for pair-verify at an address."""
    calls = types.SimpleNamespace(browse=0, dialled=[])

    async def browse(timeout):
        calls.browse += 1
        return browses.pop(0) if browses else []

    async def create(identifier, host, port):
        calls.dialled.append((identifier, host, port))
        return dial(host, port)

    install(
        monkeypatch,
        **{
            "pymobiledevice3.pair_records": types.SimpleNamespace(
                iter_remote_pair_records_by_identifier=lambda: iter([(BOUND, None, {"peer_alt_irk": b"irk"})])
            ),
            "pymobiledevice3.bonjour": types.SimpleNamespace(browse_remotepairing=browse),
            "pymobiledevice3.remote": types.ModuleType("pymobiledevice3.remote"),
            "pymobiledevice3.remote.siphash": types.SimpleNamespace(
                validate_auth_tag=lambda irk, identifier, tag: irk == b"irk" and tag == "ok"
            ),
            "pymobiledevice3.remote.tunnel_service": types.SimpleNamespace(
                PEER_ALT_IRK_KEY="peer_alt_irk", create_core_device_tunnel_service_using_remotepairing=create
            ),
        },
    )
    return calls


class Service:
    def __init__(self, host):
        self.host = host

    async def close(self):
        pass


async def test_wifi_discovery_re_asks_and_matches_only_the_bound_phone(monkeypatch):
    # The first query is lost and a stranger answers the second; the third answer is ours.
    calls = wifi_fakes(monkeypatch, [[], [answer(tag="other")], [answer()]], lambda host, port: Service(host))
    wifi = WifiTransport(browse_seconds=5)
    assert await wifi.present("00008110AAAAAAAAAAAAAAAA")
    assert calls.browse == 3 and wifi.last == {"00008110aaaaaaaaaaaaaaaa": ("192.168.4.71", 49152)}
    assert not await wifi.present("00008110-BBBBBBBBBBBBBBBB")  # No pairing record: no browse at all.
    assert calls.browse == 3
    assert wifi.matches(BOUND, {"identifier": "svc", "authTag": "ok"})
    assert not wifi.matches(BOUND, {"identifier": "svc"})


async def test_wifi_present_checks_the_last_address_before_any_browse(monkeypatch):
    calls = wifi_fakes(monkeypatch, [], lambda host, port: Service(host))
    server = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    wifi = WifiTransport(browse_seconds=0.05)
    try:
        wifi.remember(BOUND, "127.0.0.1", port)
        assert await wifi.present(BOUND) and calls.browse == 0
    finally:
        server.close()
        await server.wait_closed()
    # Nothing listens there any more: fall back to Bonjour, which finds nothing.
    assert not await wifi.present(BOUND) and calls.browse >= 1


async def test_wifi_open_dials_the_last_address_then_the_browsed_one(monkeypatch):
    def dial(host, port):
        if host == "10.0.0.9":
            raise ConnectionRefusedError()
        return Service(host)

    calls = wifi_fakes(monkeypatch, [[answer()]], dial)
    wifi = WifiTransport(browse_seconds=1)
    wifi.remember(BOUND, "10.0.0.9", 49152)  # Stale: the phone has a new DHCP address.
    service, closers, _ = await wifi.open(BOUND)
    assert service.host == "192.168.4.71" and closers == [service.close]
    assert calls.dialled == [(BOUND, "10.0.0.9", 49152), (BOUND, "192.168.4.71", 49152)]
    with raises_code("WIFI_PAIRING_REQUIRED"):
        await wifi.open("00008110-BBBBBBBBBBBBBBBB")


async def test_wifi_open_stops_at_a_locked_phone(monkeypatch):
    def dial(host, port):
        raise LockedError("The device has not been unlocked recently")

    calls = wifi_fakes(monkeypatch, [[answer(ip="192.168.4.72")]], dial)
    wifi = WifiTransport(browse_seconds=1)
    wifi.remember(BOUND, "192.168.4.71", 49152)
    with pytest.raises(LockedError):
        await wifi.open(BOUND)
    assert calls.dialled == [(BOUND, "192.168.4.71", 49152)] and calls.browse == 0
    assert classify(LockedError("The device has not been unlocked recently")).code == "PHONE_LOCKED"
    wifi_fakes(monkeypatch, [], dial)
    with raises_code("DEVICE_DISCONNECTED"):  # Nothing remembered and nothing advertised.
        await WifiTransport(browse_seconds=0.05).open(BOUND)


async def test_wifi_last_address_survives_a_restart(monkeypatch, tmp_path):
    wifi_fakes(monkeypatch, [], lambda host, port: Service(host))
    record = tmp_path / "remote-pair-record.plist"
    record.write_bytes(b"")
    monkeypatch.setitem(
        sys.modules,
        "pymobiledevice3.pair_records",
        types.SimpleNamespace(iter_remote_pair_records_by_identifier=lambda: iter([(BOUND, record, {})])),
    )
    store = tmp_path / "device" / "wifi-address.json"
    WifiTransport(store=store).remember(BOUND, "192.168.4.71", 49152)
    assert WifiTransport(store=store).last == {"00008110aaaaaaaaaaaaaaaa": ("192.168.4.71", 49152)}
    restarted = WifiTransport(store=store)
    restarted.forget(BOUND)
    assert restarted.last == {} and WifiTransport(store=store).last == {} and not record.exists()
    store.write_text("not json")
    assert WifiTransport(store=store).last == {}


def test_error_classification_is_payload_free():
    class NotPairedError(Exception):
        pass

    class ConnectionTerminatedError(Exception):
        pass

    class CoreDeviceError(Exception):
        pass

    assert classify(NotPairedError("secret device text")).code == "TRUST_REQUIRED"
    assert classify(ConnectionTerminatedError()).code == "DEVICE_DISCONNECTED"
    assert classify(CoreDeviceError("device said: private")).as_dict() == {
        "code": "COREDEVICE_ERROR",
        "message": "The phone rejected the CoreDevice request",
    }
    assert classify(RuntimeError("x")).code == "BACKEND_FAILED"
    wrapped = RuntimeError("wrapper")
    wrapped.__cause__ = ConnectionResetError()
    assert transport_lost(wrapped) and not transport_lost(ValueError())
    assert same_udid("00008110-AAAA", "00008110aaaa")


async def test_usb_watch_applies_listen_events_as_deltas_and_closes(monkeypatch, tmp_path):
    def device(devid, serial, kind="USB"):
        return types.SimpleNamespace(devid=devid, serial=serial, is_usb=kind == "USB")

    events = [
        [device(1, "A")],  # Replayed Attached for the phone already on the cable: no change.
        [device(1, "A"), device(2, "B")],
        [device(1, "A"), device(2, "B"), device(3, "C", "Network")],  # Wi-Fi sync entry: ignored.
        [device(2, "B"), device(3, "C", "Network")],
    ]
    closed = []

    class Mux:
        devices: list = []

        async def listen(self):
            pass

        async def receive_device_state_update(self):
            if not events:
                raise ConnectionError("usbmuxd went away")
            self.devices = events.pop(0)

        async def close(self):
            closed.append(True)

    async def create_mux():
        return Mux()

    async def list_devices():
        return [device(1, "A")]

    install(
        monkeypatch,
        **{"pymobiledevice3.usbmux": types.SimpleNamespace(create_mux=create_mux, list_devices=list_devices)},
    )
    monkeypatch.setattr(
        sys.modules["pymobiledevice3"], "usbmux", sys.modules["pymobiledevice3.usbmux"], raising=False
    )
    usb = UsbTransport(tmp_path)
    seen = []
    with pytest.raises(ConnectionError):
        async for serials in usb.watch():
            seen.append(serials)
    assert seen == [{"A"}, {"A"}, {"A", "B"}, {"A", "B"}, {"B"}]
    assert closed == [True]


async def test_wifi_browses_are_serialized(monkeypatch):
    records = [("00008110-AAAAAAAAAAAAAAAA", None, {"peer_alt_irk": b"irk"})]
    active, peak = [0], [0]

    async def browse(timeout):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await asyncio.sleep(0.01)
        active[0] -= 1
        return []

    install(
        monkeypatch,
        **{
            "pymobiledevice3.pair_records": types.SimpleNamespace(
                iter_remote_pair_records_by_identifier=lambda: iter(records)
            ),
            "pymobiledevice3.bonjour": types.SimpleNamespace(browse_remotepairing=browse),
            "pymobiledevice3.remote": types.ModuleType("pymobiledevice3.remote"),
            "pymobiledevice3.remote.siphash": types.SimpleNamespace(validate_auth_tag=lambda *a: False),
            "pymobiledevice3.remote.tunnel_service": types.SimpleNamespace(PEER_ALT_IRK_KEY="peer_alt_irk"),
        },
    )
    wifi = WifiTransport(browse_seconds=0.01)
    udid = records[0][0]
    await asyncio.gather(wifi.present(udid), wifi.addresses(udid), wifi.present(udid))
    assert peak[0] == 1
