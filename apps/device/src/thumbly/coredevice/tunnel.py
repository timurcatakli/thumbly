"""One root-free, in-process RSD tunnel and its RemoteServiceDiscovery handshake.

Both transports hand over a provider with ``start_tcp_tunnel()``; the tunnel terminates in
pymobiledevice3's userspace TCP/IP stack, so no kernel utun and no root are needed on the Mac.
The stack is a process-wide singleton, so this worker keeps exactly one tunnel at a time.
"""

import asyncio
import logging
from contextlib import AsyncExitStack

from thumbly.domain.errors import ThumblyError

log = logging.getLogger("thumbly.coredevice.tunnel")
_lock = asyncio.Lock()


def quiet_delayed_ack() -> bool:
    """Stop PyTCP's delayed-ACK timer from ticking on an idle connection.

    pymobiledevice3 sets ``tcp.delayed_ack.delay_ms`` to 1 so request/response services are not
    stalled by the timer, and PyTCP's ESTABLISHED timer handler re-arms that timer on every expiry
    whether or not an ACK is owed. Each open session then wakes the loop 1000 times a second
    forever: about 60% of a Cortex-A55 core with the phone connected and idle. Incoming data and
    every outgoing segment arm the timer anyway, so an expiry with nothing to acknowledge can
    simply cancel it. Returns False if the PyTCP internals are not the ones this was written for.
    """
    try:
        from pmd_pytcp.protocols.tcp.session.tcp__session__tx import TcpTxEngine
        from pmd_pytcp.protocols.tcp.tcp__seq import gt32
    except ImportError:
        return False
    original = TcpTxEngine.delayed_ack
    if getattr(original, "thumbly_quiet", False):
        return True

    def delayed_ack(self) -> None:
        session = self._session
        if not session._timer_armed("delayed_ack") and not gt32(session._rcv_seq.nxt, session._rcv_seq.una):
            session._cancel_timer("delayed_ack")
            return
        original(self)

    delayed_ack.thumbly_quiet = True  # ty: ignore[unresolved-attribute]
    TcpTxEngine.delayed_ack = delayed_ack
    return True


class Tunnel:
    def __init__(self, provider, closers, connect_deadline: float = 30):
        self.provider = provider
        self.closers = list(closers)
        self.connect_deadline = connect_deadline
        self.stack: AsyncExitStack | None = None
        self.rsd = None
        self.tun = None
        self.result = None

    async def open(self):
        from pymobiledevice3.remote import tunnel_service, userspace_tunnel
        from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService

        # Userspace TCP/IP stack: no root and no utun device, so Thumbly runs as the logged-in user.
        async with _lock:
            if userspace_tunnel.USERSPACE_ACTIVE:
                raise ThumblyError("TUNNEL_BUSY", "A previous tunnel is still shutting down")
            tunnel_service.USE_USERSPACE_TUNNEL = True  # ty: ignore[invalid-assignment]
            if not quiet_delayed_ack():
                log.warning("PyTCP internals changed; the idle tunnel may wake 1000 times a second")
            stack = AsyncExitStack()
            try:
                for closer in self.closers:
                    stack.push_async_callback(closer)
                result = await asyncio.wait_for(
                    stack.enter_async_context(self.provider.start_tcp_tunnel()), self.connect_deadline
                )
                tun = result.client.tun
                tun.set_peer(result.address)
                dial_plane = await stack.enter_async_context(
                    userspace_tunnel.UserspaceDialPlane(tun, result.address)
                )
                rsd = RemoteServiceDiscoveryService(
                    (result.address, result.port),
                    open_connection=dial_plane.dial,
                    auxiliary_metadata=result.auxiliary_metadata,
                )
                stack.push_async_callback(rsd.close)
                await asyncio.wait_for(rsd.connect(), self.connect_deadline)
            except BaseException:
                await stack.aclose()
                tunnel_service.USE_USERSPACE_TUNNEL = False
                raise
            self.stack, self.rsd, self.tun, self.result = stack, rsd, tun, result
            # Device-initiated media (the HID authentication stream) must terminate on the
            # userspace stack; the library finds that stack through these module globals.
            userspace_tunnel._active_tunnel = self  # ty: ignore[invalid-assignment]
            userspace_tunnel.USERSPACE_ACTIVE = True
            return rsd

    async def wait_closed(self):
        if self.result is not None:
            await self.result.client.wait_closed()

    async def aclose(self):
        from pymobiledevice3.remote import tunnel_service, userspace_tunnel

        async with _lock:
            stack, self.stack = self.stack, None
            if stack is None:
                return
            self.rsd = self.tun = self.result = None
            if userspace_tunnel._active_tunnel is self:
                userspace_tunnel._active_tunnel = None
                userspace_tunnel.USERSPACE_ACTIVE = False
                tunnel_service.USE_USERSPACE_TUNNEL = False
            await stack.aclose()
