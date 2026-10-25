"""Persistent CoreDevice service clients over one RSD: screenshots, HID, apps and device info.

Service connections stay open between requests. Any transport-level failure marks the session
lost so the manager rebuilds the tunnel instead of retrying on a dead connection.
"""

import asyncio
import contextlib
import io
import logging
import time

from PIL import Image

from thumbly.adapters.storage.pairing import device_tag
from thumbly.coredevice.state import State, classify, transport_lost
from thumbly.domain.errors import ThumblyError
from thumbly.domain.geometry import geometry_epoch
from thumbly.domain.imaging import encode
from thumbly.domain.models import Frame, FrameMetadata

logger = logging.getLogger(__name__)

# RSD handshake properties (device identity) and deviceinfo fields worth surfacing. Names only;
# unmapped fields are listed by key so the owner can see what the phone offers.
PEER_KEYS = {
    "ProductType": "product_type",
    "ProductVersion": "os_version",
    "BuildVersion": "os_build",
    "DeviceClass": "device_class",
    "HardwareModel": "hardware_model",
    "OSVersion": "os_version",
}
INFO_KEYS = {
    "cpuCount": "cpu_count",
    "hasActionButton": "has_action_button",
    "supportsSiri": "supports_siri",
    "supportedBiometrics": "supported_biometrics",
    "internalStorageCapacity": "internal_storage_bytes",
    "remoteServicesVersion": "remote_services_version",
}
# After a stream miss (a locked phone streams no frames), screenshots go straight to the discrete
# capture until the next input or this many seconds, instead of paying the miss every time.
STREAM_RETRY_SECONDS = 30.0


class LinkSession:
    def __init__(self, rsd, config, on_lost):
        self.rsd, self.config, self.on_lost = rsd, config, on_lost
        self.screen_factory = None
        self.info_factory = None
        self.hid = None
        self.indigo = None
        self.keyboard_id = None
        self.decoder = None
        self.content: tuple[int, int] | None = None
        self.hid_started = 0.0
        self.hid_used = 0.0
        self.last_touch = None
        self.keys_held = False
        self.hid_lock = asyncio.Lock()
        self.stream_missed = 0.0
        self.lost = False

    # ----- lifecycle -----------------------------------------------------------------

    def _fail(self, exc: BaseException) -> ThumblyError:
        if transport_lost(exc):
            logger.warning("session lost: %s", type(exc).__name__)
            self.lost = True
            self.on_lost()
        return classify(exc)

    async def _call(self, coroutine, timeout: float, fatal: bool = True):
        """Await one service call. A timeout or transport error on a required call marks the
        session lost; optional calls (``fatal=False``) only report their own failure."""
        label = getattr(coroutine, "__qualname__", "call")
        try:
            return await asyncio.wait_for(coroutine, timeout)
        except asyncio.CancelledError:
            raise
        except ThumblyError:
            raise
        except TimeoutError:
            if fatal:
                logger.warning("session lost: %s timed out after %.1fs", label, timeout)
                self.lost = True
                self.on_lost()
            raise ThumblyError("DEVICE_TIMEOUT", "The phone did not answer in time") from None
        except Exception as exc:
            if fatal:
                raise self._fail(exc) from None
            raise classify(exc) from None

    async def _open(self, factory):
        service = factory(self.rsd)
        await self._call(service.connect(), 10)
        return service

    async def _invoke(self, factory, call, timeout: float, name: str = "call", fatal: bool = True):
        """One reply-bearing CoreDevice request on a fresh RemoteXPC connection.

        The DDI daemons (dtscreencaptured, dtdeviceinfod, dtremotedisplayd) abort with
        "Attempted to send non-reply msg on the reply channel" when a second request with a reply
        arrives on the same connection, and launchd then throttles their respawn for seconds.
        Connections through the persistent tunnel are cheap, so every request gets its own.
        """
        started = time.monotonic()
        service = await self._open(factory)
        try:
            result = await self._call(call(service), timeout, fatal=fatal)
        finally:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(service.close(), 2)
        logger.info("%s answered in %.2fs", name, time.monotonic() - started)
        return result

    async def prepare(self, auto_mount: bool, on_state) -> dict:
        """Check Developer Mode and the developer disk image; mount it when allowed."""
        from pymobiledevice3.exceptions import AlreadyMountedError
        from pymobiledevice3.services.mobile_image_mounter import (
            MobileImageMounterService,
            image_type_for_device,
        )
        from pymobiledevice3.services.mobile_image_mounter import (
            auto_mount as mount_image,
        )

        developer_mode = await self._call(self.rsd.get_developer_mode_status(), 10)
        if not developer_mode:
            raise ThumblyError(
                "DEVELOPER_MODE_REQUIRED",
                "Enable Developer Mode on the phone (Settings > Privacy & Security)",
            )
        mounter = MobileImageMounterService(self.rsd)
        try:
            mounted = await self._call(mounter.is_image_mounted(image_type_for_device(self.rsd)), 20)
        finally:
            with contextlib.suppress(Exception):
                await mounter.close()
        if mounted:
            return {"developer_mode": True, "ddi_mounted": True, "mounted_now": False}
        if not auto_mount:
            raise ThumblyError("DDI_REQUIRED", "The developer disk image is not mounted")
        on_state(State.MOUNTING_DDI)
        try:
            await asyncio.wait_for(mount_image(self.rsd), self.config.ddi_mount_deadline)
        except AlreadyMountedError:
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if transport_lost(exc):
                raise self._fail(exc) from None
            raise ThumblyError("DDI_MOUNT_FAILED", "Developer disk image download or mount failed") from None
        return {"developer_mode": True, "ddi_mounted": True, "mounted_now": True}

    async def open(self):
        from pymobiledevice3.remote.core_device.device_info import DeviceInfoService
        from pymobiledevice3.remote.core_device.screen_capture_service import ScreenCaptureService

        self.screen_factory, self.info_factory = ScreenCaptureService, DeviceInfoService
        # Validates that the developer disk image services are advertised and reachable.
        await self._invoke(DeviceInfoService, lambda svc: svc.get_device_info(), 10, "deviceinfo")

    def _service(self, service, name: str):
        if service is None:
            raise ThumblyError("DEVICE_DISCONNECTED", f"The {name} service is not open")
        return service

    async def probe(self):
        await self._invoke(self.info_factory, lambda svc: svc.get_device_info(), 8, "probe")

    async def close(self):
        await self.stop_hid()

    # ----- screenshots ---------------------------------------------------------------

    async def screenshot(self, binding, options, deadline: float) -> Frame:
        started = time.monotonic()
        if not self._skip_stream(started):
            frame = await self._stream_screenshot(binding, options, started)
            if frame is not None:
                self.stream_missed = 0.0
                return frame
            # Stream miss (not started, stale, or lost): tear the stream down before the discrete
            # capture, which the running stream would otherwise wedge, then fall back to PNG.
            self.stream_missed = time.monotonic()
            await self.stop_hid()
        result = await self._invoke(
            self.screen_factory, lambda svc: svc.capture_screenshot(), deadline, "screenshot"
        )
        received = time.time()
        raw = result.get("image") if isinstance(result, dict) else None
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            raise ThumblyError("CAPTURE_FAILED", "The phone returned no image")
        try:
            image = Image.open(io.BytesIO(raw))
            image.load()
        except Exception:
            raise ThumblyError("CAPTURE_FAILED", "The phone returned an unreadable image") from None
        native_width, native_height = image.size
        epoch = geometry_epoch(native_width, native_height, device_tag(binding))
        payload = bytes(raw)
        if options.max_size or options.format != "png" or image.format != "PNG":
            if options.max_size:
                image.thumbnail((options.max_size, options.max_size))
            payload = encode(image, options.format)
        width, height = image.size
        metadata = FrameMetadata(
            geometry_epoch=epoch,
            width=width,
            height=height,
            native_width=native_width,
            native_height=native_height,
            content_rect=(0, 0, width, height),
            received_at=received,
            request_seconds=time.monotonic() - started,
            media_type=f"image/{options.format}",
        )
        return Frame(metadata, payload)

    # ----- streamed screenshots ------------------------------------------------------

    def _skip_stream(self, now: float) -> bool:
        """True while a recent miss says the stream has no frames to give; never while it runs."""
        if not self.stream_missed or self.hid_active:
            return False
        return now - self.stream_missed < STREAM_RETRY_SECONDS

    async def _content_size(self) -> tuple[int, int] | None:
        from pymobiledevice3.remote.core_device.device_info import DeviceInfoService

        try:
            info = await self._invoke(DeviceInfoService, lambda s: s.get_display_info(), 8, "displayinfo")
        except ThumblyError:
            return None
        displays = info.get("displays") if isinstance(info, dict) else None
        for display in displays or []:
            mode = display.get("currentMode") if isinstance(display, dict) else None
            size = mode.get("size") if isinstance(mode, dict) else None
            if isinstance(size, list) and len(size) == 2 and size[0] and size[1]:
                return int(size[0]), int(size[1])
        return None

    async def _ensure_stream_locked(self):
        from thumbly.coredevice.screen_stream import ScreenDecoder

        if self.decoder is not None and self.decoder.running:
            self.hid_used = time.monotonic()
            return
        if self.decoder is not None:
            # Ended on its own (a receive error): release its connection, socket and tasks first.
            stale, self.decoder = self.decoder, None
            await stale.stop()
        if self.content is None:
            self.content = await self._content_size()
        decoder = ScreenDecoder(self.rsd, self.config, self.on_lost)
        if self.content is not None:
            decoder.set_content(*self.content)
        await decoder.start()
        self.decoder = decoder
        self.hid_used = time.monotonic()

    async def _stream_screenshot(self, binding, options, started: float) -> Frame | None:
        try:
            async with self.hid_lock:
                await self._ensure_stream_locked()
        except ThumblyError:
            return None
        decoder = self.decoder
        if decoder is None or not await decoder.wait_frame(self.config.stream_start_seconds):
            return None
        # A tap's result can be on screen before the decoder reaches it: catch up first, which
        # usually takes one keyframe round trip. A cold stream starts behind, so allow the start
        # window; a frame still more than stream_frame_ttl behind falls back to a discrete capture.
        await decoder.catch_up(self.config.stream_start_seconds)
        grabbed = await decoder.grab(options.max_size)
        if grabbed is None:
            return None
        image, age, (native_width, native_height) = grabbed
        epoch = geometry_epoch(native_width, native_height, device_tag(binding))
        width, height = image.size
        logger.info("stream screenshot in %.2fs (frame age %.2fs)", time.monotonic() - started, age)
        metadata = FrameMetadata(
            geometry_epoch=epoch,
            width=width,
            height=height,
            native_width=native_width,
            native_height=native_height,
            content_rect=(0, 0, width, height),
            received_at=time.time(),
            request_seconds=time.monotonic() - started,
            source_age_seconds=round(age, 3),
            source_clock="mac hevc decode; age = lag behind screen",
            media_type=f"image/{options.format}",
        )
        return Frame(metadata, encode(image, options.format, self.config.stream_jpeg_quality))

    # ----- HID -----------------------------------------------------------------------

    @property
    def hid_active(self) -> bool:
        return self.hid is not None or (self.decoder is not None and self.decoder.running)

    async def ensure_hid(self):
        """Open the touch/keyboard/button services behind the media-stream authentication gate. The
        screenshot decoder is that stream, so input opens on top of it."""
        self.stream_missed = 0.0  # Input may have unlocked or changed the screen.
        from pymobiledevice3.remote.core_device.hid_service import UniversalHIDServiceService

        async with self.hid_lock:
            if self.hid is not None and time.monotonic() - self.hid_started > self.config.hid_refresh_seconds:
                await self._stop_hid_services()
            await self._ensure_stream_locked()
            if self.hid is not None:
                self.hid_used = time.monotonic()
                return
            hid = await self._open_hid(UniversalHIDServiceService)
            self.hid = hid
            self.hid_started = self.hid_used = time.monotonic()
            self.last_touch = None
            self.keys_held = False

    async def _open_hid(self, service_class):
        """Connect the HID service; close it again on failure. The virtual keyboard is attached
        only while typing (see ``keyboard``): while one is attached, iOS hides the on-screen
        keyboard and its QuickType bar, and with them password AutoFill."""
        hid = service_class(self.rsd)
        try:
            await self._call(hid.connect(), 10)
        except BaseException:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(hid.close(), 2)
            raise
        return hid

    async def _stop_hid_services(self):
        """Close touch/keyboard/button services without touching the decoder stream."""
        if self.hid is not None:
            # Let the last report be processed before the surfaces disappear.
            remaining = 0.25 - (time.monotonic() - self.hid_used)
            if remaining > 0:
                await asyncio.sleep(remaining)
        hid, self.hid = self.hid, None
        indigo, self.indigo = self.indigo, None
        self.keyboard_id = None
        self.last_touch = None
        self.keys_held = False
        for service in (hid, indigo):
            if service is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(service.close(), 2)

    async def _stop_hid_locked(self):
        if self.hid is not None:
            # Let the last report be processed before the surfaces disappear.
            remaining = 0.25 - (time.monotonic() - self.hid_used)
            if remaining > 0:
                await asyncio.sleep(remaining)
        hid, self.hid = self.hid, None
        decoder, self.decoder = self.decoder, None
        indigo, self.indigo = self.indigo, None
        self.keyboard_id = None
        self.last_touch = None
        self.keys_held = False
        if decoder is not None:
            await decoder.stop()
        for service in (hid, indigo):
            if service is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(service.close(), 2)
        if decoder is not None and not self.lost:
            await self._stop_stream()

    async def _stream_sessions(self) -> int | None:
        """Number of media-stream sessions the phone still holds, or None when unknown."""
        from pymobiledevice3.remote.core_device.display_service import DisplayService

        try:
            async with DisplayService(self.rsd) as display:
                status = await asyncio.wait_for(display.get_media_stream_server_status(), 5)
        except asyncio.CancelledError:
            raise
        except Exception:
            return None
        sessions = status.get("sessions") if isinstance(status, dict) else None
        return len(sessions) if isinstance(sessions, list) else None

    async def _stop_stream(self):
        """Stop the authentication stream on a fresh connection and wait until the phone reports
        no sessions. The screen-capture daemon wedges permanently (until a phone reboot) when a
        screenshot races a stream that was only abandoned, so this is never skipped."""
        from pymobiledevice3.remote.core_device.display_service import DisplayService

        started = time.monotonic()
        for attempt in range(3):
            with contextlib.suppress(Exception):
                await asyncio.wait_for(DisplayService.stop_all_streams(self.rsd), 5)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                remaining = await self._stream_sessions()
                if remaining == 0:
                    logger.info(
                        "hid stream stopped in %.2fs (attempt %d)", time.monotonic() - started, attempt + 1
                    )
                    return
                await asyncio.sleep(0.25)
        logger.warning(
            "hid stream still reported after %.1fs; screenshots may stall", time.monotonic() - started
        )

    async def stop_hid(self):
        async with self.hid_lock:
            await self._stop_hid_locked()

    async def idle_check(self):
        if self.hid_active and time.monotonic() - self.hid_used > self.config.hid_idle_seconds:
            await self.stop_hid()

    def _hid(self):
        if self.hid is None:
            raise ThumblyError("INPUT_INTERRUPTED", "The input session is not open")
        self.hid_used = time.monotonic()
        return self.hid

    async def _hid_call(self, coroutine, timeout: float = 3):
        try:
            return await asyncio.wait_for(coroutine, timeout)
        except asyncio.CancelledError:
            raise
        except ThumblyError:
            raise
        except Exception as exc:
            if isinstance(exc, TimeoutError) or transport_lost(exc):
                logger.warning("input session closed (%s)", type(exc).__name__)
                await self.stop_hid()
                raise ThumblyError("INPUT_INTERRUPTED", "The phone closed the input session") from None
            raise classify(exc) from None

    async def touch(self, state: str, x: int, y: int):
        from pymobiledevice3.remote.core_device.hid_service import (
            TOUCHSCREEN_STATE_CONTACT,
            TOUCHSCREEN_STATE_RELEASE,
        )

        hid = self._hid()
        code = TOUCHSCREEN_STATE_CONTACT if state == "contact" else TOUCHSCREEN_STATE_RELEASE
        await self._hid_call(hid.send_touchscreen(code, x, y))
        self.last_touch = (x, y) if state == "contact" else None

    async def keyboard(self, usages: list[int]):
        hid = self._hid()
        if self.keyboard_id is None:
            # Returns after the attach settle, so the first report finds the keyboard.
            self.keyboard_id = await self._hid_call(hid.create_keyboard_service(), 10)
        await self._hid_call(hid.send_keyboard(self.keyboard_id, usages))
        self.keys_held = bool(usages)

    async def ensure_buttons(self):
        """Hardware buttons ride the Indigo service alone; no media stream is needed."""
        self.stream_missed = 0.0  # Input may have unlocked or changed the screen.
        from pymobiledevice3.remote.core_device.hid_service import IndigoHIDService

        if self.indigo is None:
            self.indigo = await self._open(IndigoHIDService)
        self.hid_used = time.monotonic()

    async def button(self, page: int, usage: int, state: str):
        from pymobiledevice3.remote.core_device.hid_service import HID_BUTTON_STATE_DOWN, HID_BUTTON_STATE_UP

        await self.ensure_buttons()
        indigo = self._service(self.indigo, "button")
        code = HID_BUTTON_STATE_DOWN if state == "down" else HID_BUTTON_STATE_UP
        try:
            await asyncio.wait_for(indigo.send_button(page, usage, code), 3)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.indigo = None
            with contextlib.suppress(Exception):
                await indigo.close()
            if isinstance(exc, TimeoutError) or transport_lost(exc):
                raise ThumblyError("INPUT_INTERRUPTED", "The phone closed the button service") from None
            raise classify(exc) from None

    async def release_all(self):
        """Lift any held touch and clear the virtual keyboard. Bounded; failures propagate."""
        if self.hid is None or self.lost:
            return
        if self.last_touch is not None:
            x, y = self.last_touch
            await self.touch("release", x, y)
        if self.keys_held and self.hid is not None:
            await self.keyboard([])
        if self.keyboard_id is not None:
            # Detach the keyboard so the on-screen one comes back; the stream stays up and the
            # next input reopens the HID service without a keyboard.
            async with self.hid_lock:
                await self._stop_hid_services()

    # ----- information ---------------------------------------------------------------

    async def list_apps(self) -> list[dict]:
        from pymobiledevice3.remote.core_device.app_service import AppServiceService

        apps = await self._invoke(
            AppServiceService, lambda svc: svc.list_apps(), self.config.capture_deadline, "apps"
        )
        result = []
        for app in apps or []:
            if not isinstance(app, dict) or not app.get("bundleIdentifier"):
                continue
            result.append(
                {
                    "bundle_id": app.get("bundleIdentifier"),
                    "name": app.get("name"),
                    "version": app.get("version"),
                    "first_party": app.get("isFirstParty"),
                    "removable": app.get("isRemovable"),
                    "hidden": app.get("isHidden"),
                }
            )
        return sorted(result, key=lambda app: str(app["name"] or app["bundle_id"]).lower())

    async def launch_app(self, bundle_id: str, url: str | None = None, kill_existing: bool = False) -> dict:
        """Foreground an installed app, optionally with a URL for it to open (devicectl's
        ``--payload-url``: ``options.payloadURL = {"relative": url}``). ``kill_existing=False``
        keeps a running app's state and answers with the same process token; Safari acts on the
        URL while running, Instagram only at a cold launch (iOS 27 bench, 2026-09-29)."""
        import plistlib

        from pymobiledevice3.remote.core_device.app_service import AppServiceService

        options: dict = {
            "arguments": [],
            "environmentVariables": {},
            "standardIOUsesPseudoterminals": True,
            "startStopped": False,
            "terminateExisting": kill_existing,
            "user": {"shortName": "mobile"},
            "platformSpecificOptions": plistlib.dumps({}),
        }
        if url:
            options["payloadURL"] = {"relative": url}
        request = {
            "applicationSpecifier": {"bundleIdentifier": {"_0": bundle_id}},
            "options": options,
            "standardIOIdentifiers": {},
        }
        try:
            # A locked phone shows its passcode prompt and never answers; keep the session alive.
            result = await self._invoke(
                AppServiceService,
                lambda svc: svc.invoke("com.apple.coredevice.feature.launchapplication", request),
                10,
                "launch",
                fatal=False,
            )
        except ThumblyError as exc:
            if exc.code == "DEVICE_TIMEOUT":
                raise ThumblyError(
                    "LAUNCH_TIMEOUT", "The phone did not launch the app in 10 s; unlock it and retry", 423
                ) from None
            if exc.code == "COREDEVICE_ERROR":
                raise ThumblyError(
                    "APP_LAUNCH_FAILED", "The phone could not launch that bundle identifier", 404
                ) from None
            raise
        token = (result or {}).get("processToken", {}) if isinstance(result, dict) else {}
        executable = token.get("executableURL", {})
        return {
            "bundle_id": bundle_id,
            "pid": token.get("processIdentifier"),
            "executable": executable.get("relative") if isinstance(executable, dict) else None,
            "url": url,
            "restarted": kill_existing,
        }

    async def clipboard_get(self) -> dict:
        from pymobiledevice3.remote.core_device.pasteboard_service import PasteboardService, snapshot_text

        snapshot = await self._invoke(PasteboardService, lambda svc: svc.get(), 10, "clipboard_get")
        return {"text": snapshot_text(snapshot), "change_count": _change_count(snapshot)}

    async def clipboard_set(self, text: str) -> dict:
        """Replace the general pasteboard and read it back on a fresh connection. One bench run
        answered SET_REPLY yet kept the earlier contents, so the read-back is the real result."""
        from pymobiledevice3.remote.core_device.pasteboard_service import PasteboardService

        for attempt in range(2):
            await self._invoke(PasteboardService, lambda svc: svc.set_text(text), 10, "clipboard_set")
            current = await self.clipboard_get()
            if current["text"] == text:
                return {**current, "verified": True, "attempts": attempt + 1}
        raise ThumblyError("CLIPBOARD_MISMATCH", "The phone kept different clipboard contents")

    async def device_info(self) -> dict:
        info = await self._invoke(self.info_factory, lambda svc: svc.get_device_info(), 10, "deviceinfo")
        result: dict = {}
        properties = (getattr(self.rsd, "peer_info", None) or {}).get("Properties", {})
        if isinstance(properties, dict):
            for source, target in PEER_KEYS.items():
                if source in properties and target not in result:
                    result[target] = properties[source]
        if isinstance(info, dict):
            for source, target in INFO_KEYS.items():
                if source in info:
                    result[target] = info[source]
            display = info.get("displayInfo")
            if isinstance(display, dict):
                result["display"] = _display_summary(display)
            result["available_fields"] = sorted(str(k) for k in info)
        with contextlib.suppress(ThumblyError):
            lock = await self._invoke(self.info_factory, lambda svc: svc.get_lockstate(), 5, "lockstate")
            if isinstance(lock, dict):
                result["lock_state"] = {k: v for k, v in lock.items() if isinstance(v, (bool, int, str))}
        result["product_version"] = getattr(self.rsd, "product_version", None)
        return result


def _change_count(snapshot: dict) -> int | None:
    pasteboard = snapshot.get("pasteboard") if isinstance(snapshot, dict) else None
    metadata = (pasteboard or {}).get("metadata") if isinstance(pasteboard, dict) else None
    count = (metadata or {}).get("changeCount") if isinstance(metadata, dict) else None
    return int(count) if isinstance(count, int) else None


def _display_summary(display: dict) -> dict:
    """Keep numeric display facts (pixel size, scale) and list the remaining field names."""
    summary: dict = {}
    for key, value in display.items():
        if isinstance(value, (int, float, bool, str)):
            summary[str(key)] = value
        elif isinstance(value, dict):
            nested = {k: v for k, v in value.items() if isinstance(v, (int, float, bool, str))}
            if nested:
                summary[str(key)] = nested
    summary["available_fields"] = sorted(str(k) for k in display)
    return summary
