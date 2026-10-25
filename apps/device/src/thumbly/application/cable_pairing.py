"""Pair and bind the single USB-connected iPhone when none is bound."""

import asyncio
import contextlib
import logging
import time
from uuid import uuid4

from thumbly.adapters.storage.pairing import device_tag
from thumbly.domain.errors import ThumblyError
from thumbly.domain.models import Binding

log = logging.getLogger("thumbly.autopair")

# Longest cable long poll while nothing is pending; a pending retry or attempt shortens it.
EVENT_WAIT = 25
BOUND_CHECK = 2  # Seconds between checks that the bound phone is still bound.


class CablePairing:
    """Trust-pair and bind the single cabled phone while no phone is bound.

    iOS still requires the owner to unlock the phone and accept its Trust prompt.

    Event driven: while a phone is bound it only checks every few seconds whether that is still
    so (Use another iPhone, reset). Unbound, it follows the worker's usbmuxd attach and detach
    events (a long poll) and acts on the serials those report; retries follow the rules in
    ``_pair_and_bind``. Unplugging forgets a refused Trust so the next plug-in asks again.
    A phone the owner forgot (Use another iPhone) stays unbound until it leaves the cable, so it
    is not bound again while it is still plugged in.
    """

    def __init__(self, config, phone, bindings, journal, clock=time.monotonic):
        self.config, self.phone = config, phone
        self.bindings, self.journal, self.clock = bindings, journal, clock
        self.state = "idle"
        self.device: str | None = None
        self.last_error: dict | None = None
        self.retry_at = 0.0
        self.denied: str | None = None
        self.forgotten: str | None = None
        self.attempt: asyncio.Task | None = None
        self.task: asyncio.Task | None = None

    def status(self) -> dict:
        retry = self.retry_at - self.clock()
        return {
            "state": self.state,
            "device": self.device,
            "last_error": self.last_error,
            "retry_in": max(0, int(retry)) if retry > 0 else None,
        }

    def forget(self, udid: str) -> None:
        """The owner forgot this phone: leave it alone until it is unplugged."""
        self.forgotten = udid
        self._set("forgotten", udid)

    def _set(self, state: str, udid: str | None = None, error: dict | None = None) -> None:
        self.state, self.last_error = state, error
        self.device = (
            device_tag(Binding(udid=udid, owner_confirmed=True, portrait_locked=True)) if udid else None
        )

    async def tick(self, serials: list[str] | None = None) -> None:
        """One step. ``serials`` are the phones on the cable from the last cable event; when
        omitted they are read from the worker once."""
        if self.attempt is not None:
            if not self.attempt.done():
                return
            self.attempt = None
        if not self.config.auto_pair:
            self._set("off")
            return
        binding = self.bindings.read()
        if binding is not None:
            self._set("bound", binding.udid)
            return
        if self.clock() < self.retry_at:
            return
        if serials is None:
            serials = (await self.phone.usb_events(None, 0))["serials"]
        phones = list(serials)
        if not phones:
            self.denied = self.forgotten = None  # Replugging asks again.
            self._set("no_phone")
            return
        if len(phones) != 1:
            self._set("multiple_phones")
            return
        udid = phones[0]
        if self.forgotten == udid:
            self._set("forgotten", udid)
            return
        if self.denied == udid:
            self._set("denied", udid, self.last_error)
            return
        self.denied = None
        self.attempt = asyncio.create_task(self._pair_and_bind(udid))

    async def _pair_and_bind(self, udid: str) -> None:
        binding = Binding(udid=udid, transport="auto", owner_confirmed=True, portrait_locked=True)
        operation = str(uuid4())
        self.journal.reserve(operation, "thumbly", "autopair", {"device": device_tag(binding)})
        self.journal.update(operation, "DISPATCHED")
        self._set("pairing", udid)
        try:
            result = await self.phone.device.pair(udid)
            self._set("binding", udid)

            async def bind():
                if self.bindings.read() is not None:
                    raise ThumblyError("ALREADY_BOUND")
                current = (await self.phone.usb_events(None, 0))["serials"]
                if current != [udid]:
                    raise ThumblyError("DEVICE_DISCONNECTED", "The phone left the cable before binding")
                return await self.phone.bind(binding)

            bound = await self.phone.coordinator.observe(bind)
            self.journal.update(operation, "SUCCEEDED", {**result, **bound})
            self.retry_at = 0
            self._set("bound", udid)
        except asyncio.CancelledError:
            self.journal.update(operation, "OUTCOME_UNKNOWN")
            raise
        except Exception as exc:
            error = exc.as_dict() if isinstance(exc, ThumblyError) else {"code": "PAIRING_FAILED"}
            self.journal.update(operation, "FAILED", error)
            self.last_error = error
            code = error.get("code")
            if code == "PHONE_LOCKED":
                self.retry_at = self.clock() + 5
                self._set("locked", udid, error)
            elif code == "TRUST_DENIED":
                self.denied = udid
                self._set("denied", udid, error)
            elif code == "TRUST_REQUIRED":
                self.retry_at = self.clock() + 3
                self._set("trust_pending", udid, error)
            else:
                self.retry_at = self.clock() + 30
                self._set("failed", udid, error)
                if not isinstance(exc, ThumblyError):
                    log.warning("USB autopair failed", exc_info=True)

    def _wait_seconds(self) -> float:
        """How long the next cable poll may block: until a retry falls due or an attempt may
        have ended, never more than ``EVENT_WAIT``."""
        if self.attempt is not None:
            return 1.0
        if self.retry_at > self.clock():
            return min(max(self.retry_at - self.clock(), 0.5), EVENT_WAIT)
        return EVENT_WAIT

    async def run(self):
        changes = None
        while True:
            if self.bindings.read() is not None:
                changes = None
                with contextlib.suppress(Exception):
                    await self.tick([])  # Updates the reported state; no worker call.
                await asyncio.sleep(BOUND_CHECK)
                continue
            try:
                cable = await self.phone.usb_events(changes, 0 if changes is None else self._wait_seconds())
                changes = cable["changes"]
                await self.tick(cable["serials"])
            except asyncio.CancelledError:
                raise
            except Exception:
                changes = None
                await asyncio.sleep(2)  # Worker unavailable: retry shortly.

    def start(self):
        if self.task is None and self.config.auto_pair:
            self.task = asyncio.create_task(self.run())

    async def close(self):
        for task in (self.task, self.attempt):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self.task = self.attempt = None
