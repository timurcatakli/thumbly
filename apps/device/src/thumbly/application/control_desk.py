import asyncio
import contextlib
import logging
import secrets
import time
from dataclasses import dataclass

from thumbly.domain.errors import ThumblyError

log = logging.getLogger("thumbly.coordinator")


@dataclass
class Lease:
    owner: str
    token: str
    expires: float


@dataclass
class Work:
    run: object
    future: asyncio.Future
    operation_id: str | None = None
    owner: str | None = None
    token: str | None = None


class ControlDesk:
    """One action queue and one control lease.

    The lease lives while its holder is active: every accepted interaction with its token (a
    submitted action, a capture, acquire, resume) moves the deadline to ``lease_seconds`` after it,
    and a submitted action holds the lease until it finishes, then counts from its end. One
    watcher sleeps until the deadline and wakes only for a new or dropped lease; with no lease it
    never wakes."""

    def __init__(self, journal, device, capacity=16, lease_seconds=90):
        self.journal, self.device = journal, device
        self.queue = asyncio.Queue(capacity)
        self.lease_seconds = lease_seconds
        self.lease: Lease | None = None
        self.paused = False
        self.pending = {}
        self.current = None
        self.running = None
        self.runner = self.watcher = None
        self.release_error = None
        # Set on acquire and drop; activity only moves the deadline, which the watcher re-reads.
        self.changed = asyncio.Event()
        # Serializes drop and acquire so a drop's stop_input cannot cancel the next holder's work.
        self.control_lock = asyncio.Lock()

    async def start(self):
        self.journal.recover_interrupted()
        await self.release()
        self.runner = asyncio.create_task(self._loop())
        self.watcher = asyncio.create_task(self._watch())

    async def release(self):
        try:
            await asyncio.wait_for(self.device.release_all(), 1)
            self.release_error = None
        except Exception:
            self.release_error = "RELEASE_UNCONFIRMED"

    def held(self, lease):
        """Queued or running work under this lease keeps it alive whatever the deadline says."""
        return any(work.token == lease.token for work in self.pending.values())

    def live(self, lease=None):
        lease = lease or self.lease
        return lease is not None and (lease.expires > time.monotonic() or self.held(lease))

    def touch(self, lease=None):
        lease = lease or self.lease
        if lease is not None:
            lease.expires = max(lease.expires, time.monotonic() + self.lease_seconds)

    def check(self, owner, token, allow_paused=False):
        lease = self.lease
        if lease is None or not self.live(lease):
            raise ThumblyError("LEASE_EXPIRED")
        if lease.owner != owner or not token:
            raise ThumblyError("CONTROL_NOT_OWNED", status=403)
        if not secrets.compare_digest(lease.token, token):
            # Same owner, older token: the lease lapsed and was taken again since.
            raise ThumblyError("LEASE_EXPIRED")
        if self.paused and not allow_paused:
            raise ThumblyError("PAUSED")
        return lease

    def accept(self, owner, token):
        """A capture with a token: the holder's moves the deadline, anyone else's changes nothing."""
        lease = self.lease
        if (
            lease is not None
            and self.live(lease)
            and lease.owner == owner
            and secrets.compare_digest(lease.token, token or "")
        ):
            self.touch(lease)

    async def acquire(self, owner):
        async with self.control_lock:
            if self.lease and not self.live():
                await self._drop()
            if self.lease:
                if self.lease.owner != owner:
                    raise ThumblyError("CONTROL_BUSY")
                self.touch()
                return self.lease_status(owner, include_token=True)
            self.lease = Lease(owner, secrets.token_urlsafe(32), time.monotonic() + self.lease_seconds)
            self.changed.set()
            return self.lease_status(owner, include_token=True)

    def lease_status(self, owner=None, include_token=False):
        lease = self.lease
        if lease is not None and not self.live(lease):
            lease = None
        owned = lease is not None and lease.owner == owner
        result = {
            "active": lease is not None,
            "owned": owned,
            "remaining_seconds": max(0, lease.expires - time.monotonic()) if lease is not None else 0,
            "paused": self.paused,
            "queue_depth": self.queue.qsize(),
            "release_error": self.release_error,
        }
        if include_token and lease is not None and owned:
            result["lease_token"] = lease.token
        return result

    async def drop_control(self):
        async with self.control_lock:
            await self._drop()

    async def _drop(self):
        self.lease = None
        self.changed.set()
        await self.stop_input()

    async def stop_input(self):
        for work in list(self.pending.values()):
            if work is self.current:
                if self.running:
                    self.running.cancel()
            else:
                self.journal.update(work.operation_id, "CANCELLED")
                work.future.cancel()
                self.pending.pop(work.operation_id, None)
        # This channel bypasses captures and the action queue.
        await self.release()

    async def pause(self):
        self.paused = True
        await self.stop_input()
        return {"paused": True, "release_error": self.release_error}

    def resume(self, owner, token):
        self.check(owner, token, allow_paused=True)
        self.touch()
        self.paused = False
        return {"paused": False}

    def submit(self, operation_id, owner, token, kind, payload, run):
        self.check(owner, token)
        self.touch()
        operation, fresh = self.journal.reserve(operation_id, owner, kind, payload)
        if not fresh:
            return operation
        if self.queue.full():
            return self.journal.update(operation_id, "FAILED", {"code": "QUEUE_FULL"})
        future = asyncio.get_running_loop().create_future()
        work = Work(run, future, operation_id, owner, token)
        self.pending[operation_id] = work
        self.queue.put_nowait(work)
        return operation

    async def observe(self, run):
        if self.queue.full():
            raise ThumblyError("QUEUE_FULL", status=429)
        future = asyncio.get_running_loop().create_future()
        self.queue.put_nowait(Work(run, future))
        return await future

    async def outcome(self, operation_id, timeout):
        """Wait until queued or running work for this operation ends, or ``timeout`` passes.

        Never cancels the work, on timeout or when the waiter itself is cancelled. Every path
        writes the journal before it settles the work's future, so read the journal after."""
        work = self.pending.get(operation_id)
        if work is not None:
            await asyncio.wait({work.future}, timeout=timeout)

    async def cancel(self, operation_id, owner):
        operation = self.journal.get(operation_id, owner)
        work = self.pending.get(operation_id)
        if work:
            if work is self.current and self.running:
                self.running.cancel()
                await self.release()
            else:
                self.journal.update(operation_id, "CANCELLED")
                work.future.cancel()
                self.pending.pop(operation_id, None)
            operation = self.journal.get(operation_id, owner)
        return operation

    async def _loop(self):
        while True:
            work = await self.queue.get()
            self.current = work
            dispatched = False
            try:
                if work.future.cancelled():
                    continue
                if work.operation_id:
                    self.check(work.owner, work.token)
                    self.journal.update(work.operation_id, "DISPATCHED")
                    dispatched = True
                self.running = asyncio.create_task(work.run())
                result = await self.running
                if work.operation_id:
                    self.journal.update(work.operation_id, "SUCCEEDED", result)
                if not work.future.done():
                    work.future.set_result(result)
            except asyncio.CancelledError:
                if work.operation_id:
                    self.journal.update(work.operation_id, "OUTCOME_UNKNOWN" if dispatched else "CANCELLED")
                if not work.future.done():
                    work.future.cancel()
                if (task := asyncio.current_task()) and task.cancelling():
                    raise
            except Exception as exc:
                error = exc.as_dict() if isinstance(exc, ThumblyError) else {"code": "BACKEND_FAILED"}
                if work.operation_id:
                    self.journal.update(
                        work.operation_id, "OUTCOME_UNKNOWN" if dispatched else "CANCELLED", error
                    )
                    if not work.future.done():
                        work.future.set_result(None)
                elif not work.future.done():
                    work.future.set_exception(exc)
            finally:
                if work.operation_id:
                    self.pending.pop(work.operation_id, None)
                if work.token is not None and self.lease is not None and work.token == self.lease.token:
                    # The idle deadline counts from the end of the holder's last action.
                    self.touch()
                self.current = self.running = None
                self.queue.task_done()

    async def _watch(self):
        while True:
            self.changed.clear()
            lease = self.lease
            if lease is None:
                await self.changed.wait()
                continue
            # Work under the lease holds it with no deadline of its own; its end moves the deadline,
            # so look again one lease later. Activity moves the deadline without waking this task.
            delay = self.lease_seconds if self.held(lease) else lease.expires - time.monotonic()
            if delay > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.changed.wait(), delay)
                continue
            try:
                async with self.control_lock:
                    # Activity or a new lease that raced the timer wins.
                    if self.lease is lease and not self.live(lease):
                        log.info(
                            "control lease expired after %.0f s idle; releasing input", self.lease_seconds
                        )
                        await self._drop()
            except Exception:
                # A failing release must not stop expiry for the next lease.
                log.exception("control lease expiry failed")

    async def close(self):
        await self.pause()
        for task in (self.watcher, self.runner):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        while not self.queue.empty():
            work = self.queue.get_nowait()
            work.future.cancel()
            self.queue.task_done()
