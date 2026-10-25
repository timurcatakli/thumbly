"""API-process client for the persistent CoreDevice worker over the group-restricted Unix socket."""

import base64

from thumbly.domain.models import Frame, FrameMetadata
from thumbly.workers.protocol import rpc


class WorkerClient:
    def __init__(self, config):
        self.config = config
        self.socket = config.runtime_dir / "device.sock"

    async def connect(self, binding):
        return await rpc(self.socket, {"op": "connect", "binding": binding.model_dump()}, 5)

    async def disconnect(self):
        return await rpc(self.socket, {"op": "disconnect"}, 10)

    async def unbind(self):
        return await rpc(self.socket, {"op": "unbind"}, 15)

    @property
    def hold(self):
        # A request while disconnected waits for one search window, then for a connect that
        # started inside it; one Bonjour browse may overrun the window by a second.
        return self.config.switch_hold_seconds * 2 + self.config.search_window_seconds + 1

    async def capture(self, binding, options):
        result = await rpc(
            self.socket,
            {"op": "capture", "binding": binding.model_dump(), "options": options.model_dump()},
            self.config.capture_deadline + 3 + self.hold,
        )
        return Frame(
            FrameMetadata.model_validate(result["metadata"]), base64.b64decode(result["image"], validate=True)
        )

    async def begin(self, binding, kinds=None):
        request = {"op": "begin", "binding": binding.model_dump(), "kinds": list(kinds or [])}
        return (await rpc(self.socket, request, 15 + self.hold))["generation"]

    async def report(self, generation, kind, value):
        await rpc(self.socket, {"op": "report", "generation": generation, "kind": kind, "value": list(value)})

    async def gesture(self, generation, points):
        request = {"op": "gesture", "generation": generation, "points": [list(p) for p in points]}
        return await rpc(self.socket, request, float(points[-1][0]) + 5 + self.hold)

    async def release_all(self):
        await rpc(self.socket, {"op": "release"}, 0.8)

    async def health(self):
        return await rpc(self.socket, {"op": "status"})

    async def verify(self, binding):
        return await rpc(self.socket, {"op": "verify", "binding": binding.model_dump()}, 20)

    async def recover(self, binding):
        return await rpc(self.socket, {"op": "recover", "binding": binding.model_dump()}, 30)

    async def list_apps(self, binding):
        return (await rpc(self.socket, {"op": "apps", "binding": binding.model_dump()}, 40 + self.hold))[
            "apps"
        ]

    async def launch_app(self, binding, bundle_id, url=None, kill_existing=False):
        request = {
            "op": "launch_app",
            "binding": binding.model_dump(),
            "bundle_id": bundle_id,
            "url": url,
            "kill_existing": bool(kill_existing),
        }
        return await rpc(self.socket, request, 25 + self.hold)

    async def clipboard_get(self, binding):
        return await rpc(
            self.socket, {"op": "clipboard_get", "binding": binding.model_dump()}, 15 + self.hold
        )

    async def clipboard_set(self, binding, text):
        request = {"op": "clipboard_set", "binding": binding.model_dump(), "text": text}
        return await rpc(self.socket, request, 20 + self.hold)

    async def device_info(self, binding):
        return await rpc(self.socket, {"op": "device_info", "binding": binding.model_dump()}, 15 + self.hold)

    async def discover(self):
        return await rpc(self.socket, {"op": "discover"}, 30)

    async def usb_events(self, since, timeout):
        request = {"op": "usb_events", "since": since, "timeout": timeout}
        return await rpc(self.socket, request, timeout + 5)

    async def usb_phones(self):
        return (await rpc(self.socket, {"op": "usb_phones"}, 15))["usb"]

    async def pair(self, udid):
        # The worker waits up to 60 s for the Trust prompt plus the RemotePairing handshake.
        return await rpc(self.socket, {"op": "pair", "udid": udid, "owner_confirmed": True}, 130)

    async def developer_mode(self, udid):
        return await rpc(self.socket, {"op": "developer_mode", "udid": udid, "owner_confirmed": True}, 40)

    async def mount_ddi(self):
        return await rpc(self.socket, {"op": "mount_ddi"}, 10)
