"""In-process stand-in for the CoreDevice worker: synthetic frames and a bounded report log."""

import asyncio
import time
from collections import deque

from PIL import Image, ImageDraw

from thumbly.adapters.storage.pairing import device_tag
from thumbly.domain.errors import ThumblyError
from thumbly.domain.geometry import geometry_epoch
from thumbly.domain.imaging import encode
from thumbly.domain.models import Frame, FrameMetadata, identifier

APPS = [
    {"bundle_id": "com.apple.Preferences", "name": "Settings", "version": "1.0", "first_party": True},
    {"bundle_id": "com.apple.mobilesafari", "name": "Safari", "version": "17.0", "first_party": True},
    {"bundle_id": "com.apple.mobilenotes", "name": "Notes", "version": "4.11", "first_party": True},
]


class SimulatedDevice:
    UDID = "simulated-phone"
    SCROLL_MAX = 1200  # points of content below the first screen
    FLICK_SECONDS, FLICK_GAIN = 0.3, 5.5  # a quick release flings, as iOS does

    def __init__(self):
        self.count = 0
        self.delay = 0.05
        self.failure = None
        self.reports = deque(maxlen=4096)
        self.generation = None
        self.connected = True
        self.releases = 0
        self.transport = None
        self.since = time.time()
        self.clipboard: dict = {"text": None, "change_count": 0}
        # A scrolling screen: the numbered circles move up by the vertical distance of each
        # drag (16-bit touch axes mapped onto the 844-point frame) until SCROLL_MAX, while the
        # two title rows stay put like a real app's header.
        self.scroll = 0
        self.touch_start: float | None = None
        # What usb_phones() reports: the simulated phone, an iPhone 13 on iOS 27 by default.
        self.cabled = [{"udid": self.UDID, "product_type": "iPhone14,5", "product_version": "27.0"}]

    def connection(self):
        return {
            "state": "connected" if self.connected else "disconnected",
            "transport": self.transport,
            "since": self.since,
            "attempts": 0,
            "last_error": None,
            "device_present": self.connected,
            "developer_mode": True,
            "ddi_mounted": True,
            "hid_active": self.generation is not None,
            "simulated": True,
        }

    async def connect(self, binding):
        self.transport = binding.transport
        self.connected = True
        self.since = time.time()
        return {"connection": self.connection()}

    async def disconnect(self):
        self.connected = False
        return {"connection": self.connection()}

    async def unbind(self):
        self.transport = None
        return await self.disconnect()

    def _require(self):
        if not self.connected:
            raise ThumblyError("DEVICE_DISCONNECTED")

    async def capture(self, binding, options):
        started = time.monotonic()
        await asyncio.sleep(self.delay)
        if self.failure:
            raise self.failure
        self._require()
        self.count += 1
        image = Image.new("RGB", (390, 844), "#13212f")
        draw = ImageDraw.Draw(image)
        draw.text((28, 40), "Thumbly / SIMULATOR", fill="#61dec2", font_size=23)
        draw.text((28, 85), f"Capture {self.count} / {time.strftime('%H:%M:%S')}", fill="white", font_size=19)
        for i in range(9 + 3 * (self.SCROLL_MAX // 190)):
            x, y = 65 + (i % 3) * 130, 250 + (i // 3) * 190 - self.scroll
            if -40 < y < 884:
                # Size and tint vary per circle so rows do not repeat every 190 points.
                r = 18 + (i * 7) % 16
                draw.ellipse((x - r, y - r, x + r, y + r), fill=(0x31 + (i * 29) % 90, 0x51, 0x6B))
                draw.text((x - 5, y - 10), str(i + 1), fill="white", font_size=20)
        if options.max_size:
            image.thumbnail((options.max_size, options.max_size))
        w, h = image.size
        return Frame(
            FrameMetadata(
                geometry_epoch=geometry_epoch(390, 844, device_tag(binding)),
                width=w,
                height=h,
                native_width=390,
                native_height=844,
                content_rect=(0, 0, w, h),
                received_at=time.time(),
                request_seconds=time.monotonic() - started,
                media_type=f"image/{options.format}",
            ),
            encode(image, options.format),
        )

    async def verify(self, binding):
        self._require()
        return {"present": True, "trusted": True, "transport": binding.transport}

    async def begin(self, binding, kinds=None):
        self._require()
        await self.release_all()
        self.generation = identifier()
        return self.generation

    async def report(self, generation, kind, value):
        if not self.connected or generation != self.generation:
            raise ThumblyError("INPUT_INTERRUPTED")
        self.reports.append((kind, list(value)))
        if kind == "touch":
            state, _, y = value
            if state == "contact" and self.touch_start is None:
                self.touch_start = y
            elif state == "release" and self.touch_start is not None:
                moved = round((self.touch_start - y) / 65535 * 844)
                self.scroll = max(0, min(self.SCROLL_MAX, self.scroll + moved))
                self.touch_start = None

    async def gesture(self, generation, points):
        """One paced touch gesture. A jump released within FLICK_SECONDS scrolls FLICK_GAIN
        times its length, like iOS momentum; a slower drag scrolls one to one."""
        for _, state, x, y in points:
            if not self.connected or generation != self.generation:
                raise ThumblyError("INPUT_INTERRUPTED")
            self.reports.append(("touch", [state, x, y]))
        moved = round((points[0][3] - points[-1][3]) / 65535 * 844)
        if float(points[-1][0]) - float(points[0][0]) <= self.FLICK_SECONDS:
            moved = round(moved * self.FLICK_GAIN)
        self.scroll = max(0, min(self.SCROLL_MAX, self.scroll + moved))
        self.touch_start = None
        return {
            "reports": len(points),
            "seconds": float(points[-1][0]),
            "median_interval_ms": 33,
            "max_gap_ms": 33,
        }

    async def release_all(self):
        self.generation = None
        self.releases += 1
        self.reports.append(("keyboard", []))
        self.reports.append(("release", []))

    async def health(self):
        return {
            "state": "unavailable" if (self.failure or not self.connected) else "ready",
            "connection": self.connection(),
            "simulated": True,
        }

    async def recover(self, binding):
        self.failure = None
        self.connected = True
        await self.release_all()
        return await self.health()

    async def list_apps(self, binding):
        self._require()
        return [dict(app) for app in APPS]

    async def launch_app(self, binding, bundle_id, url=None, kill_existing=False):
        self._require()
        if not any(app["bundle_id"] == bundle_id for app in APPS):
            raise ThumblyError("APP_NOT_FOUND", "No installed app has that bundle identifier", 404)
        record = ("launch_app", bundle_id) if url is None else ("open_url", bundle_id, url, kill_existing)
        self.reports.append(record)
        return {
            "bundle_id": bundle_id,
            "pid": 4000 + APPS.index(next(a for a in APPS if a["bundle_id"] == bundle_id)),
        }

    async def clipboard_get(self, binding):
        self._require()
        return dict(self.clipboard)

    async def clipboard_set(self, binding, text):
        self._require()
        self.clipboard = {"text": text, "change_count": int(self.clipboard["change_count"] or 0) + 1}
        self.reports.append(("clipboard_set", len(text)))
        return {**self.clipboard, "verified": True}

    async def device_info(self, binding):
        self._require()
        return {
            "product_type": "Simulator",
            "marketing_name": "Thumbly simulated iPhone",
            "os_version": "0.0",
            "os_build": "SIM",
            "device_class": "iPhone",
            "locked": False,
            "display": {"native_width": 390, "native_height": 844},
            "transport": self.transport,
            "simulated": True,
        }

    async def usb_events(self, since, timeout):
        # The simulated cable never changes, so a poll for news simply runs out.
        if since is not None:
            await asyncio.sleep(timeout)
        return {"changes": 0, "serials": [p["udid"] for p in self.cabled], "listening": True}

    async def usb_phones(self):
        await asyncio.sleep(self.delay)
        return list(self.cabled)

    async def discover(self):
        await asyncio.sleep(self.delay)
        return {
            "usb": [{"udid": self.UDID, "connection": "USB"}],
            "wifi_paired": [],
            "wifi_reachable": [],
            "note": "Simulated backend; identifiers are for the owner's explicit binding.",
            "simulated": True,
        }

    async def pair(self, udid):
        await asyncio.sleep(self.delay)
        return {"trusted": True, "wifi_pairing": True, "udid": udid, "simulated": True}

    async def developer_mode(self, udid):
        await asyncio.sleep(self.delay)
        return {"developer_mode": True, "udid": udid, "simulated": True}

    async def mount_ddi(self):
        await asyncio.sleep(self.delay)
        return {"mounted": True, "simulated": True}
