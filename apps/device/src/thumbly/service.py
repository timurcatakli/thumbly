import asyncio
import contextlib
import os
import signal

from thumbly.adapters.device.simulated import SimulatedDevice
from thumbly.adapters.device.worker_client import WorkerClient
from thumbly.adapters.storage.activity_log import ActivityLog
from thumbly.adapters.storage.pairing import PairingStore
from thumbly.adapters.system.host import MacHost
from thumbly.application.cable_pairing import CablePairing
from thumbly.application.control_desk import ControlDesk
from thumbly.application.phone import Phone
from thumbly.domain.models import Binding
from thumbly.domain.profiles import ProfileStore

DEVICE_NAME = "Thumbly"


class Service:
    def __init__(self, config):
        self.config = config
        config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.journal = ActivityLog(config.state_dir / "thumbly.sqlite3")
        self.bindings = PairingStore(config.state_dir)
        device = SimulatedDevice() if config.backend == "simulated" else WorkerClient(config)
        if config.backend == "simulated" and self.bindings.read() is None:
            self.bindings.write(
                Binding(udid="simulated-phone", transport="usb", owner_confirmed=True, portrait_locked=True)
            )
        self.coordinator = ControlDesk(self.journal, device, config.queue_size, config.lease_seconds)
        self.host = MacHost()
        self.profiles = ProfileStore(config.state_dir / "apps")
        self.phone = Phone(
            config,
            device,
            self.host,
            self.journal,
            self.bindings,
            self.coordinator,
            profiles=self.profiles,
        )
        self.autopair = self.phone.autopair = CablePairing(config, self.phone, self.bindings, self.journal)
        self.journal.label_owner("thumbly", "Thumbly")  # Its own work, such as USB auto-pairing.
        self.auth_provider, self.gateway = build_agent_access(self)
        self.lock = None

    async def factory_reset(self) -> bool:
        """Back to first-time setup. True when done in process (the simulator). Otherwise the
        pairing, AI clients and activity are erased and the service stops; launchd starts it again."""
        if self.config.backend == "simulated":
            self.bindings.delete()
            return True
        # The worker forgets the bound phone's USB trust and Wi-Fi pairing records first.
        with contextlib.suppress(Exception):
            await self.phone.device.unbind()
        asyncio.get_running_loop().call_later(1, self._erase_and_stop)
        return False

    def _erase_and_stop(self):
        self.journal.close()
        state = self.config.state_dir
        for name in ("binding.json", "thumbly.sqlite3", "thumbly.sqlite3-shm", "thumbly.sqlite3-wal"):
            (state / name).unlink(missing_ok=True)
        (state / "device" / "wifi-address.json").unlink(missing_ok=True)
        for record in (state / "device" / "lockdown").glob("*.plist"):
            record.unlink(missing_ok=True)
        os.kill(os.getpid(), signal.SIGTERM)

    async def start(self):
        import fcntl

        self.config.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (self.config.runtime_dir / "api.lock").open("w")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            self.lock = None
            raise RuntimeError("Thumbly is already running for this user") from None
        if self.config.backend == "simulated":
            binding = self.bindings.read()
            if binding is not None:
                await self.phone.device.connect(binding)
        await self.coordinator.start()
        self.gateway.start()
        if self.config.backend != "simulated":
            self.autopair.start()

    async def close(self):
        await self.autopair.close()
        await self.gateway.close()
        await self.coordinator.close()
        self.journal.close()
        if self.lock:
            self.lock.close()


def build_agent_access(service):
    """Build the OAuth provider and gateway for consents, clients, and the MCP bridge."""
    from thumbly.interfaces.agent_gateway import AgentGateway, OAuthProvider
    from thumbly.interfaces.mcp_server import SCOPES

    config = service.config
    issuer = config.public_url or f"http://127.0.0.1:{config.local_mcp_port}"
    provider = OAuthProvider(service.journal, issuer, scopes=SCOPES)
    return provider, AgentGateway(service, provider)
