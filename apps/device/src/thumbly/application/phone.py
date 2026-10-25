import base64
import inspect
import time
from collections import OrderedDict

from thumbly.adapters.storage.pairing import device_tag
from thumbly.application import actions, recovery, scroll_capture
from thumbly.application.shortcuts import validate_shortcut
from thumbly.domain.apps import match_apps
from thumbly.domain.capabilities import capabilities
from thumbly.domain.errors import ThumblyError
from thumbly.domain.imaging import encode
from thumbly.domain.models import SEND_TEXT_BYTES, Action, Binding, CaptureOptions, FullPageOptions
from thumbly.domain.profiles import ProfileStore

TOUCH_KINDS = {"tap", "long_press", "swipe"}
SAFARI = "com.apple.mobilesafari"
RECENT_FRAMES = 8  # Frames a touch may cite: one per client easily, few enough to stay small.
# Finished full-page captures kept in memory for their GET, newest last. Images never enter
# the journal; two is enough for one client's retry while another client's capture lands.
FULL_PAGE_RESULTS = 2


class Phone:
    def __init__(
        self,
        config,
        device,
        system,
        journal,
        bindings,
        coordinator,
        profiles=None,
        autopair=None,
    ):
        self.config, self.device = config, device
        self.system, self.journal, self.bindings = system, journal, bindings
        self.coordinator = coordinator
        self.autopair = autopair
        self.profiles = profiles or ProfileStore()
        self.frame = None  # The latest capture, whoever asked for it.
        self.frame_time = 0.0
        # Captures since the last input, by frame id: a tap may cite any of them within
        # frame_ttl, so one client's screenshot does not expire the frame another client is
        # acting on. Any input clears them, since the screen may have changed.
        self.recent_frames: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self.full_pages: OrderedDict[str, dict] = OrderedDict()

    def binding(self):
        binding = self.bindings.read()
        if binding is None:
            raise ThumblyError("BINDING_REQUIRED", "Bind an explicit, owner-confirmed phone identity")
        return binding

    def capabilities(self):
        binding = self.bindings.read()
        profile = self.journal.profile(device_tag(binding)) if binding else {}
        return capabilities(list(profile.get("shortcuts", {})))

    async def status(self, owner=None):
        binding = self.bindings.read()
        components = await recovery.health({"device": self.device, "network": self.system})
        return {
            "backend": self.config.backend,
            "device": device_tag(binding) if binding else None,
            "transport": binding.transport if binding else None,
            "setup_required": binding is None,
            "autopair": self.autopair.status() if self.autopair is not None else None,
            "control": self.coordinator.lease_status(owner),
            "connection": components["device"].get("connection", {"state": "unavailable"}),
            "components": components,
            "profile": self.journal.profile(device_tag(binding)) if binding else {},
        }

    async def screenshot(self, options=None):
        options = options or CaptureOptions()
        if self.coordinator.paused:
            # The emergency brake covers sight too: a paused phone is not shown to anyone.
            raise ThumblyError("PAUSED")

        async def capture():
            try:
                frame = await self.device.capture(self.binding(), options)
                self._seen(frame.metadata)
                return frame
            except BaseException:
                self._forget_frames()
                raise

        return await self.coordinator.observe(capture)

    def full_page(self, operation_id, owner, token, options: FullPageOptions | None = None):
        """Scroll the current screen, capturing every page, and join them.

        A journaled mutation under the lease: it drags the content, so it needs control and
        cancels like input. The record holds counts and geometry only; the images wait in
        memory for ``full_page_result`` under the same operation ID."""
        options = options or FullPageOptions()
        self.coordinator.check(owner, token)
        payload = options.model_dump()
        recorded = self.journal.existing(operation_id, owner, "full_page", payload)
        if recorded is not None:
            return recorded
        binding = self.binding()
        capture_options = CaptureOptions(format=options.format, max_size=options.max_size)
        probe_options = CaptureOptions(format="jpeg", max_size=scroll_capture.PROBE_SIZE)

        async def capture(probing=False):
            self.coordinator.check(owner, token)
            if self.binding() != binding:
                raise ThumblyError("IDENTITY_MISMATCH")
            try:
                frame = await self.device.capture(binding, probe_options if probing else capture_options)
            except BaseException:
                self._forget_frames()
                raise
            if not probing:
                self._seen(frame.metadata)
            return frame

        async def probe():
            return await capture(probing=True)

        async def swipe(metadata, y_from, y_to):
            action = Action(
                kind="swipe",
                x=0.5,
                y=y_from,
                x2=0.5,
                y2=y_to,
                seconds=scroll_capture.SWIPE_SECONDS,
                hold=scroll_capture.SWIPE_HOLD,
                frame_id=metadata.frame_id,
                geometry_epoch=metadata.geometry_epoch,
            )
            points = actions.swipe_path(action, scroll_capture.SWIPE_RATE, scroll_capture.SWIPE_DWELL)
            result = await actions.execute_gesture(
                self.device, binding, points, lambda: self.coordinator.check(owner, token)
            )
            return result.get("gesture", {})

        async def run():
            result = await scroll_capture.capture_full_page(
                capture,
                swipe,
                probe,
                max_pages=options.max_pages,
                settle=options.settle,
                do_stitch=options.stitch,
            )
            pages = [
                {
                    "metadata": page.metadata.model_dump(),
                    "shift": page.shift,
                    "matched": page.matched,
                    "difference": page.difference,
                    "header": page.header,
                    "footer": page.footer,
                }
                for page in result.pages
            ]
            summary = {
                "pages": pages,
                "page_count": len(pages),
                "header": result.header,
                "footer": result.footer,
                "stopped": result.stopped,
                "swipes": result.swipes,
                "lags": result.lags,
                "cadence": result.cadence,
                "seconds": round(result.seconds, 3),
                "notes": result.notes,
                "current": result.current.model_dump(),
                "stitched": None,
            }
            images = {
                "pages": [
                    {**entry, "image": base64.b64encode(page.image).decode()}
                    for entry, page in zip(pages, result.pages)
                ]
            }
            if result.stitched is not None:
                width, height = result.stitched.size
                summary["stitched"] = {
                    "width": width,
                    "height": height,
                    "media_type": f"image/{options.format}",
                }
                images["stitched"] = {
                    **summary["stitched"],
                    "image": base64.b64encode(encode(result.stitched, options.format, quality=85)).decode(),
                }
            self.full_pages[operation_id] = {**summary, **images}
            while len(self.full_pages) > FULL_PAGE_RESULTS:
                self.full_pages.popitem(last=False)
            return summary

        return self.coordinator.submit(operation_id, owner, token, "full_page", payload, run)

    def full_page_result(self, operation_id, owner):
        """The images of a finished full-page capture, for the session that owns the operation."""
        record = self.journal.get(operation_id, owner)
        result = self.full_pages.get(operation_id)
        if result is None:
            if record["state"] in {"QUEUED", "DISPATCHED"}:
                raise ThumblyError("OPERATION_PENDING", "The capture is still running", 409)
            raise ThumblyError("FULL_PAGE_UNAVAILABLE", "Images are gone or the capture failed", 404)
        return {"operation": record, **result}

    async def apps(self, query: str = "", include_hidden: bool = True):
        apps = await self.coordinator.observe(lambda: self.device.list_apps(self.binding()))
        known = set(self.profiles.ids())
        return [
            {**app, "has_profile": app.get("bundle_id") in known}
            for app in match_apps(apps, query, include_hidden)
        ]

    def app_profile(self, bundle_id: str) -> dict:
        return self.profiles.get(bundle_id).describe()

    def app_profiles(self) -> list[dict]:
        return [
            {
                "bundle_id": profile.bundle_id,
                "name": profile.name,
                "summary": profile.summary,
                "deep_links": sorted(profile.deep_links),
                "workflows": sorted(profile.workflows),
            }
            for profile in (self.profiles.get(b) for b in self.profiles.ids())
        ]

    def open_link(self, operation_id, owner, token, bundle_id, link, params):
        """Resolve a profile deep link and open it: deterministic entry into an app screen."""
        profile = self.profiles.get(bundle_id)
        url = profile.link(link, {k: str(v) for k, v in params.items()})
        web = url.partition(":")[0].lower() in {"http", "https"}
        action = Action(
            kind="open_url",
            url=url,
            bundle_id="" if web and bundle_id == SAFARI else bundle_id,
            cold_launch=profile.cold_launch_for_url,
        )
        return self.action(operation_id, owner, token, action)

    async def device_info(self):
        return await self.coordinator.observe(lambda: self.device.device_info(self.binding()))

    async def discover(self):
        """Enumerate candidate phones for the owner's explicit choice; never binds or pairs."""
        return await self.device.discover()

    async def usb_phones(self) -> list[dict]:
        """Phones on the cable (model and iOS version only), cached for five seconds."""
        now = time.monotonic()
        cached = getattr(self, "_usb_cache", None)
        if cached is not None and now - cached[0] < 5:
            return cached[1]
        try:
            phones = await self.device.usb_phones()
        except Exception:
            phones = []
        self._usb_cache = (now, phones)
        return phones

    async def usb_events(self, since: int | None, timeout: float) -> dict:
        """Cable long poll from the worker: serials on the cable once they change."""
        return await self.device.usb_events(since, timeout)

    def _seen(self, metadata) -> None:
        self.frame, self.frame_time = metadata, time.monotonic()
        self.recent_frames[metadata.frame_id] = (metadata.geometry_epoch, self.frame_time)
        while len(self.recent_frames) > RECENT_FRAMES:
            self.recent_frames.popitem(last=False)

    def _forget_frames(self) -> None:
        """The screen is unknown again (a failed capture, a new or forgotten phone)."""
        self.frame = None
        self.recent_frames.clear()

    def validate_frame(self, action):
        if action.kind not in TOUCH_KINDS:
            return
        epoch, seen = self.recent_frames.get(action.frame_id or "", ("", float("-inf")))
        if action.geometry_epoch != epoch or time.monotonic() - seen > self.config.frame_ttl:
            raise ThumblyError("FRAME_EXPIRED", "Capture a new portrait screenshot before touch input")

    def action(self, operation_id, owner, token, action):
        if action.kind == "send_text" and len(action.text.encode()) > SEND_TEXT_BYTES:
            # Refused before the lease check and the journal, whichever door it came through.
            raise ThumblyError(
                "REQUEST_TOO_LARGE", f"send_text takes at most {SEND_TEXT_BYTES} UTF-8 bytes", 413
            )
        self.coordinator.check(owner, token)
        recorded = self.journal.existing(operation_id, owner, action.kind, action.model_dump())
        if recorded is not None:
            return recorded
        self.validate_frame(action)
        binding = self.binding()
        if action.kind in {"launch_app", "open_url"}:
            events = None
        elif action.kind == "send_text":
            events = actions.chord("v", ["cmd"])
        elif action.kind == "swipe":
            # One paced gesture from the worker instead of one report per round trip.
            events = []
            points = actions.swipe_path(action)
        else:
            # Only shortcuts depend on the owner's validated list; skip that lookup otherwise.
            shortcuts = self.capabilities()["shortcuts"] if action.kind == "shortcut" else []
            events = actions.plan(action, shortcuts)

        async def run():
            self.validate_frame(action)
            if self.binding() != binding:
                raise ThumblyError("IDENTITY_MISMATCH")
            if events is None:
                self.coordinator.check(owner, token)
                if action.kind == "open_url":
                    web = action.url.partition(":")[0].lower() in {"http", "https"} and not action.bundle_id
                    bundle = SAFARI if web else action.bundle_id
                    kill = action.cold_launch and not web
                    process = await self.device.launch_app(binding, bundle, action.url, kill_existing=kill)
                else:
                    process = await self.device.launch_app(binding, action.bundle_id)
                self._forget_frames()  # Input may have changed the screen: every frame is stale.
                return {"dispatched": True, "visual_confirmation_required": True, "process": process}
            extra = {}
            if action.kind == "send_text":
                self.coordinator.check(owner, token)
                clipboard = await self.device.clipboard_set(binding, action.text)
                # Never journal the text itself: keep only the clipboard's metadata.
                meta = {k: v for k, v in clipboard.items() if k != "text"}
                extra = {"clipboard_replaced": True, "characters": len(action.text), "clipboard": meta}
            if action.kind == "swipe":
                result = await actions.execute_gesture(
                    self.device, binding, points, lambda: self.coordinator.check(owner, token)
                )
            else:
                result = await actions.execute(
                    self.device, binding, events, lambda: self.coordinator.check(owner, token)
                )
            self._forget_frames()  # Input may have changed the screen: every frame is stale.
            return {**result, **extra}

        return self.coordinator.submit(operation_id, owner, token, action.kind, action.model_dump(), run)

    def tap(self, operation_id, owner, token, **coordinates):
        return self.action(operation_id, owner, token, Action(kind="tap", **coordinates))

    def long_press(self, operation_id, owner, token, **coordinates):
        return self.action(operation_id, owner, token, Action(kind="long_press", **coordinates))

    def swipe(self, operation_id, owner, token, **coordinates):
        return self.action(operation_id, owner, token, Action(kind="swipe", **coordinates))

    def type_text(self, operation_id, owner, token, text):
        return self.action(operation_id, owner, token, Action(kind="type_text", text=text))

    def key(self, operation_id, owner, token, key, modifiers=()):
        return self.action(operation_id, owner, token, Action(kind="key", key=key, modifiers=list(modifiers)))

    def shortcut(self, operation_id, owner, token, name):
        return self.action(operation_id, owner, token, Action(kind="shortcut", shortcut=name))

    def button(self, operation_id, owner, token, name):
        return self.action(operation_id, owner, token, Action(kind="button", button=name))

    def send_text(self, operation_id, owner, token, text):
        return self.action(operation_id, owner, token, Action(kind="send_text", text=text))

    async def clipboard(self):
        return await self.coordinator.observe(lambda: self.device.clipboard_get(self.binding()))

    def open_url(self, operation_id, owner, token, url, bundle_id=""):
        return self.action(operation_id, owner, token, Action(kind="open_url", url=url, bundle_id=bundle_id))

    def launch_app(self, operation_id, owner, token, bundle_id):
        return self.action(operation_id, owner, token, Action(kind="launch_app", bundle_id=bundle_id))

    async def acquire_control(self, owner):
        return await self.coordinator.acquire(owner)

    async def release_control(self, owner, token):
        self.coordinator.check(owner, token, allow_paused=True)
        await self.coordinator.drop_control()
        return {"released": True}

    async def pause(self):
        return await self.coordinator.pause()

    def resume(self, owner, token):
        return self.coordinator.resume(owner, token)

    async def control(self, operation_id, owner, command, token=None, target=None):
        """One journaled control command. Pause needs no lease (the safety exception); the
        others need the caller's current token, paused or not. A replayed operation ID returns
        its record; a replayed successful acquire also returns the caller's lease, token included."""
        if command == "cancel" and target is None:
            raise ThumblyError("INVALID_REQUEST", "Cancellation requires a target operation ID", 400)
        coordinator = self.coordinator
        if command not in {"acquire", "pause"}:
            coordinator.check(owner, token, allow_paused=True)
        if command not in {"acquire", "release", "pause", "resume", "cancel"}:
            raise ThumblyError("UNKNOWN_OPERATION", status=404)
        record, fresh = self.journal.reserve(operation_id, owner, command, {"target": target})
        if not fresh:
            if command == "acquire" and record["state"] == "SUCCEEDED":
                record = {**record, "control": coordinator.lease_status(owner, include_token=True)}
            return record
        methods = {
            "acquire": lambda: self.acquire_control(owner),
            "release": lambda: self.release_control(owner, token),
            "pause": self.pause,
            "resume": lambda: self.resume(owner, token),
            "cancel": lambda: self.cancel(target, owner),
        }
        self.journal.update(operation_id, "DISPATCHED")
        try:
            result = methods[command]()
            if inspect.isawaitable(result):
                result = await result
        except ThumblyError as exc:
            self.journal.update(operation_id, "FAILED", exc.as_dict())
            raise
        except Exception:
            self.journal.update(operation_id, "FAILED", {"code": "BACKEND_FAILED"})
            raise
        public = {k: v for k, v in result.items() if k != "lease_token"}
        record = self.journal.update(operation_id, "SUCCEEDED", public)
        if command == "acquire":
            record["control"] = result
        return record

    def operation_status(self, operation_id, owner):
        return self.journal.get(operation_id, owner)

    async def cancel(self, operation_id, owner):
        return await self.coordinator.cancel(operation_id, owner)

    async def bind(self, binding: Binding):
        verification = await self.device.verify(binding)
        await self.device.release_all()
        self.bindings.write(binding)
        self._forget_frames()
        connection = await self.device.connect(binding)
        return {"device": device_tag(binding), "bound": True, "verification": verification, **connection}

    @staticmethod
    def _confirmed_udid(payload):
        udid = payload.get("udid")
        if payload.get("owner_confirmed") is not True or not isinstance(udid, str) or not udid:
            raise ThumblyError(
                "OWNER_CONFIRMATION_REQUIRED",
                "Name the phone by udid and set owner_confirmed to true",
                400,
            )
        return udid

    def setup(self, operation_id, owner, token, kind, payload):
        # Trust-changing kinds fail fast, before anything is queued or journalled.
        if kind in {"pair", "developer_mode"}:
            self._confirmed_udid(payload)

        async def run():
            if kind == "pair":
                return await self.device.pair(self._confirmed_udid(payload))
            if kind == "developer_mode":
                return await self.device.developer_mode(self._confirmed_udid(payload))
            if kind == "mount_ddi":
                self.binding()
                return await self.device.mount_ddi()
            if kind == "bind":
                return await self.bind(Binding.model_validate(payload))
            if kind == "transport":
                current = self.binding()
                transport = payload.get("transport")
                if transport not in {"usb", "wifi", "auto"}:
                    raise ThumblyError("INVALID_REQUEST", "transport must be usb, wifi or auto", 400)
                return await self.bind(current.model_copy(update={"transport": transport}))
            if kind == "unbind":
                # Forget the bound phone and every pairing record. Setup over USB is required again.
                self._forget_frames()
                current = self.bindings.read()
                result = await self.device.unbind()
                if current is not None and self.autopair is not None:
                    self.autopair.forget(current.udid)  # Before the delete, so no tick binds it again.
                self.bindings.delete()
                return {"bound": False, **result}
            if kind == "recover":
                self._forget_frames()
                binding = self.binding()
                return await recovery.recover({"device": self.device}, binding)
            if kind == "validate_shortcut":
                return validate_shortcut(self.journal, self.binding(), payload)
            raise ThumblyError("UNKNOWN_SETUP_ACTION", status=400)

        return self.coordinator.submit(operation_id, owner, token, kind, payload, run)
