"""Auto-pairing follows cable events while no phone is bound, never a timer."""

import asyncio
from types import SimpleNamespace

from thumbly.application.cable_pairing import CablePairing
from thumbly.domain.errors import ThumblyError

UDID = "00008110-000A1B2C3D4E5F60"


class Cable:
    """Worker long poll: answers at once for None, else when the counter moves or on timeout."""

    def __init__(self):
        self.changes, self.serials, self.calls = 0, [], []
        self.moved = asyncio.Event()

    async def usb_events(self, since, timeout):
        self.calls.append((since, timeout))
        if since == self.changes:
            moved = self.moved
            try:
                await asyncio.wait_for(moved.wait(), timeout)
            except TimeoutError:
                pass
        return {"changes": self.changes, "serials": list(self.serials)}

    def plug(self, serials):
        self.serials, self.changes = serials, self.changes + 1
        moved, self.moved = self.moved, asyncio.Event()
        moved.set()


class Bindings:
    def __init__(self):
        self.value = None

    def read(self):
        return self.value


class Journal:
    def reserve(self, *args):
        pass

    def update(self, *args):
        pass


def build(errors=()):
    cable, bindings = Cable(), Bindings()
    pairs = []
    errors = list(errors)

    async def pair(udid):
        pairs.append(udid)
        if errors:
            raise errors.pop(0)
        return {"trusted": True}

    async def bind(binding):
        bindings.value = binding
        return {"bound": True}

    async def observe(function):
        return await function()

    phone = SimpleNamespace(
        usb_events=cable.usb_events,
        device=SimpleNamespace(pair=pair),
        bind=bind,
        coordinator=SimpleNamespace(observe=observe),
    )
    config = SimpleNamespace(auto_pair=True)
    autopair = CablePairing(config, phone, bindings, Journal())
    return autopair, cable, bindings, pairs


async def wait_for(predicate, timeout=2):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


async def test_no_worker_calls_while_a_phone_is_bound(monkeypatch):
    from thumbly.application import cable_pairing as autopair_module

    monkeypatch.setattr(autopair_module, "BOUND_CHECK", 0.02)
    autopair, cable, bindings, pairs = build()
    autopair.start()
    try:
        cable.plug([UDID])
        await wait_for(lambda: autopair.state == "bound")
        assert pairs == [UDID] and bindings.value.udid == UDID
        calls = len(cable.calls)
        await asyncio.sleep(0.1)
        assert len(cable.calls) == calls  # Bound: no cable polls.
        bindings.value = None  # Use another iPhone / reset.
        await wait_for(lambda: len(cable.calls) > calls)
    finally:
        await autopair.close()


async def test_attach_event_starts_pairing_and_locked_phone_retries_after_5_s():
    autopair, cable, bindings, pairs = build([ThumblyError("PHONE_LOCKED", "unlock")])
    clock = [100.0]
    autopair.clock = lambda: clock[0]
    autopair.start()
    try:
        await wait_for(lambda: autopair.state == "no_phone")
        cable.plug([UDID])  # Attach event: no tick needed.
        await wait_for(lambda: autopair.state == "locked")
        assert pairs == [UDID]
        await asyncio.sleep(0.05)
        assert pairs == [UDID]  # Waits for the retry time, not a 2 s tick.
        assert cable.calls[-1][1] <= 5
        clock[0] += 5
        cable.plug([UDID])
        await wait_for(lambda: autopair.state == "bound")
        assert pairs == [UDID, UDID]
    finally:
        await autopair.close()


async def test_a_refused_trust_is_asked_again_after_replugging():
    autopair, cable, bindings, pairs = build([ThumblyError("TRUST_DENIED", "denied")])
    autopair.start()
    try:
        cable.plug([UDID])
        await wait_for(lambda: autopair.state == "denied")
        cable.plug([UDID])  # Still on the cable: not asked again.
        await asyncio.sleep(0.05)
        assert pairs == [UDID]
        cable.plug([])
        await wait_for(lambda: autopair.state == "no_phone")
        cable.plug([UDID])
        await wait_for(lambda: autopair.state == "bound")
        assert pairs == [UDID, UDID] and bindings.value.udid == UDID
    finally:
        await autopair.close()


async def test_a_forgotten_phone_is_not_bound_again_until_it_is_unplugged():
    autopair, cable, bindings, pairs = build()
    autopair.start()
    try:
        cable.plug([UDID])
        await wait_for(lambda: autopair.state == "bound")
        autopair.forget(UDID)
        bindings.value = None  # Use another iPhone: the binding is gone, the phone is still plugged in.
        cable.plug([UDID])
        await wait_for(lambda: autopair.state == "forgotten")
        await asyncio.sleep(0.05)
        assert pairs == [UDID] and bindings.value is None
        cable.plug([])
        await wait_for(lambda: autopair.state == "no_phone")
        cable.plug([UDID])
        await wait_for(lambda: autopair.state == "bound")
        assert pairs == [UDID, UDID]
    finally:
        await autopair.close()
