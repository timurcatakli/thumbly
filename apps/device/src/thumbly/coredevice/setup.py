"""Explicit owner setup: discovery, USB trust plus Wi-Fi pairing record, Developer Mode.

These are the only code paths that may show a prompt on the phone. Normal capture and input
never pair.
"""

import asyncio
import contextlib

from thumbly.coredevice.state import classify
from thumbly.domain.errors import ThumblyError


def require_owner(request):
    if request.get("owner_confirmed") is not True:
        raise ThumblyError("OWNER_CONFIRMATION_REQUIRED")


async def discover(usb, wifi) -> dict:
    from pymobiledevice3.pair_records import iter_remote_paired_identifiers

    devices = await usb.devices()
    paired = sorted(set(iter_remote_paired_identifiers()))
    reachable = []
    for identifier in paired:
        with contextlib.suppress(Exception):
            if await wifi.present(identifier):
                reachable.append(identifier)
    return {
        "usb": [
            {"udid": device.serial, "connection": device.connection_type}
            for device in devices
            if device.is_usb
        ],
        "wifi_paired": paired,
        "wifi_reachable": reachable,
        "note": "Identifiers are for the owner's explicit binding; nothing here selects a phone implicitly.",
    }


async def usb_phones(usb) -> list[dict]:
    """Phones on the cable with what lockdown tells an unpaired host: model and iOS version.

    Used by the dashboard's cable status and auto-pairing; no identity beyond model and
    version leaves the worker.
    """
    result = []
    for device in await usb.devices():
        if not device.is_usb:
            continue
        entry = {"udid": device.serial, "product_type": None, "product_version": None}
        try:
            lockdown = await asyncio.wait_for(usb.lockdown(device.serial, autopair=False), 5)
            try:
                entry["product_type"] = getattr(lockdown, "product_type", None)
                entry["product_version"] = getattr(lockdown, "product_version", None)
            finally:
                with contextlib.suppress(Exception):
                    await lockdown.close()
        except Exception:
            pass  # Presence alone is still useful.
        result.append(entry)
    return result


async def pair(usb, udid: str, pair_timeout: float = 60) -> dict:
    """Establish lockdown trust (Trust prompt on the phone) and the promptless RemotePairing record."""
    from pymobiledevice3.exceptions import RemotePairingCompletedError
    from pymobiledevice3.remote.tunnel_service import RemotePairingLockdownService

    if not await usb.present(udid):
        raise ThumblyError("DEVICE_DISCONNECTED", "Connect the phone over USB for pairing")
    try:
        lockdown = await asyncio.wait_for(
            usb.lockdown(udid, autopair=True, pair_timeout=pair_timeout), pair_timeout + 10
        )
    except Exception as exc:
        name = type(exc).__name__
        if isinstance(exc, TimeoutError) or name == "PairingDialogResponsePendingError":
            raise ThumblyError(
                "TRUST_REQUIRED", "Unlock the iPhone and tap Trust when prompted", 409
            ) from None
        if name == "PasswordRequiredError":
            raise ThumblyError("PHONE_LOCKED", "Unlock the iPhone to show the Trust prompt", 409) from None
        if name == "UserDeniedPairingError":
            raise ThumblyError("TRUST_DENIED", "Trust was declined on the iPhone", 409) from None
        raise classify(exc) from None
    try:
        if not lockdown.paired:
            raise ThumblyError("TRUST_REQUIRED", "The trust prompt was not accepted on the phone")
        wifi = False
        service = await RemotePairingLockdownService.create(lockdown)
        try:
            try:
                await asyncio.wait_for(service.connect(autopair=True), 30)
            except RemotePairingCompletedError:
                pass
            wifi = True
        finally:
            with contextlib.suppress(Exception):
                await service.close()
        return {"trusted": True, "wifi_pairing": wifi}
    finally:
        await lockdown.close()


async def enable_developer_mode(usb, udid: str) -> dict:
    from pymobiledevice3.exceptions import DeviceHasPasscodeSetError
    from pymobiledevice3.services.amfi import AmfiService

    if not await usb.present(udid):
        raise ThumblyError("DEVICE_DISCONNECTED", "Connect the phone over USB")
    lockdown = await asyncio.wait_for(usb.lockdown(udid), 15)
    try:
        if not lockdown.paired:
            raise ThumblyError("TRUST_REQUIRED")
        amfi = AmfiService(lockdown)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(amfi.reveal_developer_mode_option_in_ui(), 10)
        try:
            await asyncio.wait_for(amfi.enable_developer_mode(enable_post_restart=False), 15)
        except DeviceHasPasscodeSetError:
            return {
                "enabled": False,
                "reason": "PASSCODE_SET",
                "instruction": "Enable Developer Mode in Settings > Privacy & Security, then confirm after restart",
            }
        return {
            "enabled": True,
            "restart_required": True,
            "instruction": "Confirm the Developer Mode prompt on the phone after it restarts",
        }
    finally:
        await lockdown.close()
