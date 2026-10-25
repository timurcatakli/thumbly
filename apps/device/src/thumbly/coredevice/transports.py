"""USB and Wi-Fi transports. Each locates the explicitly bound phone and yields a tunnel provider.

Both are root-free: the USB path uses the CoreDeviceProxy lockdown service (iOS 17.4+), the
Wi-Fi path uses the RemotePairing service, dialled at the phone's last known address or found
over Bonjour. Neither ever pairs implicitly; ``autopair`` is only enabled by the explicit owner
setup in ``setup.py``.
"""

import asyncio
import json
from collections.abc import AsyncGenerator
from pathlib import Path

from thumbly.coredevice.state import classify
from thumbly.domain.errors import ThumblyError

# Wi-Fi discovery: one mDNS query per sub-browse, a direct TCP check of the last known address,
# and the pair-verify deadline at that address.
QUERY_SECONDS = 1.0
DIRECT_SECONDS = 2.0
OPEN_SECONDS = 15.0


def _key(udid: str) -> str:
    return udid.replace("-", "").lower()


def same_udid(left: str, right: str) -> bool:
    return _key(left) == _key(right)


class UsbTransport:
    name = "usb"

    def __init__(self, cache_dir: Path, poll_seconds: float = 2.0):
        self.cache_dir = cache_dir
        self.poll_seconds = poll_seconds

    async def devices(self) -> list:
        from pymobiledevice3 import usbmux

        try:
            return await asyncio.wait_for(usbmux.list_devices(), 3)
        except Exception:
            return []

    async def present(self, udid: str) -> bool:
        return any(device.is_usb and same_udid(device.serial, udid) for device in await self.devices())

    async def serials(self) -> set[str]:
        """Serials of the phones on the cable right now (one usbmuxd ListDevices)."""
        return {device.serial for device in await self.devices() if device.is_usb}

    async def watch(self) -> AsyncGenerator[set[str]]:
        """Serials on the cable, yielded at once and again after every usbmuxd attach or detach.

        One Listen connection is held open and usbmuxd pushes the events, so nothing is polled.
        The first set comes from a ListDevices taken after Listen is accepted; each event is then
        applied to it as a delta, so the replayed Attached messages for phones already on the
        cable change nothing. Raises when usbmuxd refuses Listen or drops the connection; the
        caller falls back to ``serials`` polling. The connection is closed on exit.
        """
        from pymobiledevice3 import usbmux

        mux = await asyncio.wait_for(usbmux.create_mux(), 3)
        try:
            await asyncio.wait_for(mux.listen(), 3)
            current = await self.serials()
            yield set(current)
            while True:
                before = {device.devid: device for device in mux.devices}
                await mux.receive_device_state_update()
                after = {device.devid: device for device in mux.devices}
                for devid in before.keys() - after.keys():
                    current.discard(before[devid].serial)
                for devid in after.keys() - before.keys():
                    if after[devid].is_usb:
                        current.add(after[devid].serial)
                yield set(current)
        finally:
            await mux.close()

    async def lockdown(self, udid: str, autopair: bool = False, pair_timeout: float | None = None):
        from pymobiledevice3.lockdown import create_using_usbmux

        self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        return await create_using_usbmux(
            serial=udid,
            connection_type="USB",
            autopair=autopair,
            pair_timeout=pair_timeout,
            pairing_records_cache_folder=self.cache_dir,
        )

    async def open(self, udid: str):
        from pymobiledevice3.exceptions import InvalidServiceError
        from pymobiledevice3.remote.tunnel_service import CoreDeviceTunnelProxy

        lockdown = await asyncio.wait_for(self.lockdown(udid), 15)
        try:
            if not lockdown.paired:
                raise ThumblyError("TRUST_REQUIRED", "Unlock the bound phone and run explicit owner pairing")
            try:
                provider = await asyncio.wait_for(CoreDeviceTunnelProxy.create(lockdown), 15)
            except InvalidServiceError:
                raise ThumblyError(
                    "USB_TUNNEL_UNSUPPORTED", "iOS 17.4 or later is required for the USB CoreDevice tunnel"
                ) from None
        except BaseException:
            await lockdown.close()
            raise
        details = {"product_version": getattr(lockdown, "product_version", None)}
        return provider, [provider.close, lockdown.close], details

    def forget(self, udid: str | None = None):
        """Drop cached lockdown trust records; the phone shows the Trust prompt at the next pairing."""
        for path in self.cache_dir.glob("*.plist"):
            if udid is None or same_udid(path.stem, udid):
                path.unlink(missing_ok=True)

    async def verify(self, udid: str) -> dict:
        present = await self.present(udid)
        trusted = False
        if present:
            lockdown = await asyncio.wait_for(self.lockdown(udid), 15)
            try:
                trusted = bool(lockdown.paired)
            finally:
                await lockdown.close()
        return {"present": present, "trusted": trusted}


class WifiTransport:
    name = "wifi"

    def __init__(self, browse_seconds: float = 3.0, store: Path | None = None):
        self.browse_seconds = browse_seconds
        # One Bonjour browse at a time: the search window, the dashboard's address lookup and
        # the Wi-Fi tunnel open all share the radio's mDNS traffic.
        self.browse_lock = asyncio.Lock()
        # The address the bound phone last answered on, tried before any Bonjour browse: a
        # direct connect needs no multicast. Only the phone's own pairing record can complete
        # pair-verify there, so a stale address costs one failed connect. Kept in ``store``
        # across restarts, so the first connect after boot needs no Bonjour either.
        self.store = store
        self.last: dict[str, tuple[str, int]] = self._load()

    def _load(self) -> dict[str, tuple[str, int]]:
        if self.store is None:
            return {}
        try:
            data = json.loads(self.store.read_text())
            return {str(key): (str(host), int(port)) for key, (host, port) in data.items()}
        except (OSError, ValueError, TypeError):
            return {}

    def _save(self) -> None:
        if self.store is None:
            return
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = self.store.with_suffix(".tmp")
            temporary.write_text(json.dumps({key: list(value) for key, value in self.last.items()}))
            temporary.replace(self.store)
        except OSError:
            pass  # Only an optimisation: Bonjour still finds the phone.

    def records(self, udid: str) -> list[dict]:
        from pymobiledevice3.pair_records import iter_remote_pair_records_by_identifier

        return [
            record
            for identifier, _path, record in iter_remote_pair_records_by_identifier()
            if same_udid(identifier, udid)
        ]

    def paired(self, udid: str) -> bool:
        return bool(self.records(udid))

    def forget(self, udid: str | None = None):
        """Drop RemotePairing records so Wi-Fi mode needs a fresh USB pairing."""
        from pymobiledevice3.pair_records import iter_remote_pair_records_by_identifier

        for identifier, path, _record in list(iter_remote_pair_records_by_identifier()):
            if udid is None or same_udid(identifier, udid):
                path.unlink(missing_ok=True)
        for key in [key for key in self.last if udid is None or key == _key(udid)]:
            del self.last[key]
        self._save()

    def _irks(self, udid: str) -> list[bytes]:
        from pymobiledevice3.remote.tunnel_service import PEER_ALT_IRK_KEY

        return [record[PEER_ALT_IRK_KEY] for record in self.records(udid) if record.get(PEER_ALT_IRK_KEY)]

    def matches(self, udid: str, properties: dict) -> bool:
        """Whether a RemotePairing advertisement comes from the bound phone (its rotating
        identifier is signed with the pairing record's IRK)."""
        from pymobiledevice3.remote.siphash import validate_auth_tag

        identifier, tag = properties.get("identifier"), properties.get("authTag")
        return bool(identifier and tag) and any(
            validate_auth_tag(irk, identifier, tag) for irk in self._irks(udid)
        )

    def remember(self, udid: str, host: str, port: int) -> None:
        if self.last.get(_key(udid)) != (host, port):
            self.last[_key(udid)] = (host, port)
            self._save()

    async def _browse(self, udid: str) -> list:
        """Advertisements of the bound phone, re-asking once a second for ``browse_seconds``.

        Each sub-browse sends one mDNS query; Wi-Fi multicast is neither acknowledged nor
        retransmitted, so a single query is easily lost. Stops at the first answer and remembers
        the address it came from."""
        from pymobiledevice3.bonjour import browse_remotepairing

        if not self._irks(udid):
            return []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.browse_seconds
        found: list = []
        async with self.browse_lock:
            while not found and (remaining := deadline - loop.time()) > 0:
                try:
                    answers = await browse_remotepairing(timeout=min(QUERY_SECONDS, remaining))
                except Exception:
                    return []
                found = [answer for answer in answers if self.matches(udid, answer.properties)]
        for answer in found:
            ipv4 = [a for a in answer.addresses if ":" not in str(a.ip)]
            for address in ipv4 or answer.addresses:
                self.remember(udid, str(getattr(address, "full_ip", address.ip)), answer.port)
                break
        return found

    async def _reachable(self, udid: str) -> bool:
        """A TCP connect to the last address the phone answered on. It proves only that
        something listens there; ``open`` verifies the phone's identity cryptographically."""
        address = self.last.get(_key(udid))
        if address is None:
            return False
        try:
            _reader, writer = await asyncio.wait_for(asyncio.open_connection(*address), DIRECT_SECONDS)
        except (OSError, TimeoutError):
            return False
        writer.close()
        return True

    async def present(self, udid: str) -> bool:
        if not self._irks(udid):
            return False
        return await self._reachable(udid) or bool(await self._browse(udid))

    async def addresses(self, udid: str) -> list[str]:
        """IP addresses the paired phone advertises for RemotePairing right now."""
        found = await self._browse(udid)
        return sorted({str(address.ip) for answer in found for address in answer.addresses})

    async def open(self, udid: str):
        """Pair-verify with the phone at its last known address, else where Bonjour finds it.

        A locked phone refuses with PHONE_LOCKED; that is final for this attempt, since any
        other address would refuse the same way."""
        from pymobiledevice3.remote.tunnel_service import (
            create_core_device_tunnel_service_using_remotepairing,
        )

        identifier = next(
            (identifier for identifier in self._identifiers() if same_udid(identifier, udid)), None
        )
        if identifier is None:
            raise ThumblyError(
                "WIFI_PAIRING_REQUIRED", "Run explicit owner pairing over USB once before using Wi-Fi mode"
            )
        tried = None
        for step in ("cached", "browsed"):
            if step == "browsed":
                await self._browse(udid)
            address = self.last.get(_key(udid))
            if address is None or address == tried:
                continue
            tried = address
            try:
                service = await asyncio.wait_for(
                    create_core_device_tunnel_service_using_remotepairing(identifier, *address), OPEN_SECONDS
                )
            except Exception as exc:
                if classify(exc).code == "PHONE_LOCKED":
                    raise
                self.last.pop(_key(udid), None)
                self._save()
                continue
            return service, [service.close], {}
        raise ThumblyError("DEVICE_DISCONNECTED", "The paired phone is not reachable over Wi-Fi")

    def _identifiers(self) -> list[str]:
        from pymobiledevice3.pair_records import iter_remote_pair_records_by_identifier

        return [identifier for identifier, _path, _record in iter_remote_pair_records_by_identifier()]

    async def verify(self, udid: str) -> dict:
        return {"present": await self.present(udid), "trusted": self.paired(udid)}
