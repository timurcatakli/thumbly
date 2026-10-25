"""The userspace tunnel must not keep an idle phone connection busy."""

import sys
import types

from thumbly.coredevice.tunnel import quiet_delayed_ack


def gt32(a, b):
    return 0 < (a - b) & 0xFFFF_FFFF < 0x8000_0000


def fake_pytcp(monkeypatch):
    calls = []

    class TcpTxEngine:
        def __init__(self, session):
            self._session = session

        def delayed_ack(self):  # PyTCP: flush an owed ACK, then re-arm unconditionally.
            calls.append("original")
            self._session.armed = True

    tx = types.ModuleType("tx")
    tx.TcpTxEngine = TcpTxEngine  # ty: ignore[unresolved-attribute]
    seq = types.ModuleType("seq")
    seq.gt32 = gt32  # ty: ignore[unresolved-attribute]
    monkeypatch.setitem(sys.modules, "pmd_pytcp.protocols.tcp.session.tcp__session__tx", tx)
    monkeypatch.setitem(sys.modules, "pmd_pytcp.protocols.tcp.tcp__seq", seq)
    return TcpTxEngine, calls


class Session:
    def __init__(self, nxt, una, armed=False):
        self._rcv_seq = types.SimpleNamespace(nxt=nxt, una=una)
        self.armed, self.cancelled = armed, 0

    def _timer_armed(self, name):
        return self.armed

    def _cancel_timer(self, name):
        self.cancelled += 1


def test_expired_delayed_ack_with_nothing_owed_stops_ticking(monkeypatch):
    engine, calls = fake_pytcp(monkeypatch)
    assert quiet_delayed_ack() and quiet_delayed_ack()  # Idempotent: wraps once.

    idle = Session(nxt=1000, una=1000)
    engine(idle).delayed_ack()
    assert (idle.cancelled, idle.armed, calls) == (1, False, [])

    owed = Session(nxt=1500, una=1000)  # Data arrived and is not yet acknowledged.
    engine(owed).delayed_ack()
    running = Session(nxt=1000, una=1000, armed=True)  # Timer still counting down.
    engine(running).delayed_ack()
    assert calls == ["original", "original"] and owed.cancelled == running.cancelled == 0


def test_missing_pytcp_is_reported(monkeypatch):
    monkeypatch.setitem(sys.modules, "pmd_pytcp.protocols.tcp.session.tcp__session__tx", None)
    assert quiet_delayed_ack() is False
