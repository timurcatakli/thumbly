"""In-process bridge from the MCP tools and the dashboard to the phone core.

One per caller (an OAuth client or the dashboard): it holds that caller's owner identity and
lease token and calls ``Phone`` directly. Leases, the journal, the queue and Pause stay in
``Phone`` and the ``Coordinator``; requests are validated with the same ``domain.models``.
"""

import asyncio
import base64
import contextlib
import copy
import inspect
import logging
import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import anyio
from pydantic import ValidationError

from thumbly.domain.errors import ThumblyError
from thumbly.domain.models import (
    ActionRequest,
    CaptureOptions,
    ControlRequest,
    FullPageRequest,
    LinkRequest,
    SetupRequest,
)

log = logging.getLogger("thumbly.bridge")

# Long-edge pixel bound for images the bridge returns: the screenshot tool default and every
# post-action observation. Touch coordinates are normalized, so a smaller image loses nothing
# but transfer time; pass a larger max_size to thumbly_screenshot when small text matters.
OBSERVATION_MAX_SIZE = 1536
# Pre-dispatch refusals that are not argument mistakes, so the model does not "fix and resend".
REJECTED_INSTRUCTIONS = {
    "CONTROL_BUSY": "Another client controls the phone right now; nothing ran. Wait for its lease to "
    "lapse (see status.control.remaining_seconds) and retry, or report it.",
    "LEASE_EXPIRED": "Control lapsed after a pause in activity; nothing ran. The screen may have changed: "
    "take a fresh screenshot, then act again. The next action takes control if the phone is free.",
    "PAUSED": (
        "The owner paused AI control: nothing ran and the screen cannot be captured. Only resume "
        "through thumbly_control when the user intends it."
    ),
}
# A token from before the lease lapsed while another client took the phone: same advice.
REJECTED_INSTRUCTIONS["CONTROL_NOT_OWNED"] = REJECTED_INSTRUCTIONS["LEASE_EXPIRED"]
# Thumbly no longer holds the lease under these: the bridge forgets its token and acquires anew.
LOST_CONTROL = {"LEASE_EXPIRED", "CONTROL_NOT_OWNED"}
PENDING = {"QUEUED", "DISPATCHED"}


class BridgeError(Exception):
    def __init__(
        self,
        code: str,
        operation_id: str | None = None,
        status: int | None = None,
        *,
        rejected: bool = False,
    ):
        super().__init__(code)
        self.code, self.operation_id, self.status = code, operation_id, status
        # Refused before the mutation was recorded: nothing reached the phone.
        self.rejected = rejected

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code}
        if self.rejected:
            result.update(
                dispatched=False,
                instruction=REJECTED_INSTRUCTIONS.get(
                    self.code,
                    "Rejected before dispatch; nothing ran on the phone and no operation was "
                    "recorded. Correct the arguments and send the request again.",
                ),
            )
        elif self.operation_id:
            result.update(
                operation_id=self.operation_id, instruction="Inspect operation_status; do not replay input."
            )
        return result


def public_code(code: object) -> str:
    """Only short, plain error codes cross this boundary, never messages or typed content."""
    if not isinstance(code, str) or not code.replace("_", "").isalnum() or len(code) > 80:
        return "API_REJECTED"
    return code


class PhoneChannel:
    """One caller's bridge. Holds the lease token only; Thumbly keeps the lease alive while this
    caller acts or captures with it and lets it lapse after ``lease_seconds`` of quiet, so there
    is no timer here."""

    def __init__(
        self,
        phone,
        *,
        owner: str,
        wait_seconds: float = 30,
        transport: str = "streamable-http",
        observation: dict | None = None,
    ):
        self.phone, self.coordinator = phone, phone.coordinator
        self.owner = owner
        self.transport = transport
        self.wait_seconds = wait_seconds
        # Capture options for the post-action image. Bounded so a phone-resolution frame does not
        # ride every action result; models downscale beyond this anyway.
        self.observation = {"format": "jpeg", "max_size": OBSERVATION_MAX_SIZE, **(observation or {})}
        self.lease: str | None = None
        self.control_lock = asyncio.Lock()
        # Set once the gateway drops this bridge (revocation, shutdown); every later call refuses.
        self.closed = False

    async def _run(
        self, call: Callable[[], Any], operation_id: str | None = None, *, entry: bool = True
    ) -> Any:
        """Run one phone call and map its errors. Results are copied so a caller that edits
        them (the MCP layer pops images) never edits state Thumbly keeps. ``entry`` is false for
        the reads that finish a call already in flight: they still report the true outcome after
        the bridge was dropped."""
        if entry and self.closed:
            raise BridgeError("LOGIN_REQUIRED", operation_id, 401)
        try:
            result = call()
            if inspect.isawaitable(result):
                result = await result
        except ThumblyError as exc:
            raise BridgeError(public_code(exc.code), operation_id, exc.status) from None
        except ValidationError:
            # Pydantic details include raw inputs. Do not echo text or credentials.
            raise BridgeError("INVALID_REQUEST", operation_id, 400) from None
        except Exception as exc:
            log.warning("phone call failed: %s", type(exc).__name__)
            raise BridgeError("BACKEND_FAILED", operation_id) from None
        return copy.deepcopy(result)

    # -- reads -------------------------------------------------------------------------

    async def status(self) -> dict:
        result = await self._run(lambda: self.phone.status(self.owner))
        # Keep the saved score useful without flooding the model with 1,000 trial rows.
        result.get("profile", {}).get("calibration", {}).pop("samples", None)
        result["mcp"] = {"transport": self.transport}
        return result

    async def capabilities(self) -> dict:
        return await self._run(self.phone.capabilities)

    async def apps(self, query: str = "", include_hidden: bool = True) -> dict:
        query = query[:80]
        found = await self._run(lambda: self.phone.apps(query, include_hidden))
        return {"apps": found, "query": query, "matched": len(found)}

    async def app_profiles(self) -> dict:
        return {"profiles": await self._run(self.phone.app_profiles)}

    async def app_profile(self, bundle_id: str) -> dict:
        return await self._run(lambda: self.phone.app_profile(bundle_id))

    async def device_info(self) -> dict:
        return await self._run(self.phone.device_info)

    async def clipboard(self) -> dict:
        return await self._run(self.phone.clipboard)

    async def discover(self) -> dict:
        return await self._run(self.phone.discover)

    async def operation(self, operation_id: str, *, entry: bool = True) -> dict:
        return await self._run(
            lambda: self.phone.operation_status(operation_id, self.owner), operation_id, entry=entry
        )

    async def screenshot(self, options: dict | None = None) -> dict:
        async def capture():
            frame = await self.phone.screenshot(CaptureOptions.model_validate(options or {}))
            # The holder's token marks the capture as activity; a stale one changes nothing.
            if self.lease:
                self.coordinator.accept(self.owner, self.lease)
            return {
                "metadata": frame.metadata.model_dump(mode="json"),
                "image": base64.b64encode(frame.image).decode(),
            }

        return await self._run(capture)

    # -- control -----------------------------------------------------------------------

    async def _control(self, command: str, operation_id: str, target: str | None = None) -> dict:
        def call():
            request = ControlRequest.model_validate(
                {"operation_id": operation_id, "lease_token": self.lease, "target": target}
            )
            return self.phone.control(request.operation_id, self.owner, command, request.lease_token, target)

        return await self._run(call)

    def forget(self, exc: BridgeError):
        if exc.code in LOST_CONTROL:
            self.lease = None

    async def _acquire(self) -> dict:
        result = await self._control("acquire", str(uuid4()))
        self.lease = result.get("control", {}).get("lease_token")
        if not self.lease:
            raise BridgeError("BACKEND_FAILED")
        return result

    async def ensure_control(self):
        """Take the lease when this caller holds none. CONTROL_BUSY comes back before any dispatch."""
        async with self.control_lock:
            if self.lease is None:
                await self._acquire()

    def _released(self) -> dict:
        return {"released": True, "control": self.coordinator.lease_status(self.owner)}

    async def control(self, command: str, operation_id: str, target: str | None = None) -> dict:
        if command == "pause":
            return await self._control(command, operation_id)
        async with self.control_lock:
            if command == "release" and self.lease is None:
                # Nothing held here (never acquired, or it lapsed): already released.
                return self._released()
            if command in {"resume", "cancel"} and self.lease is None:
                await self._acquire()
            try:
                if command == "acquire":
                    result = await self._acquire()
                else:
                    result = await self._control(command, operation_id, target)
            except BridgeError as exc:
                self.forget(exc)
                if command == "release" and exc.code in LOST_CONTROL:
                    return self._released()
                raise
            if command == "release":
                self.lease = None
            result.get("control", {}).pop("lease_token", None)
            return result

    # -- mutations ---------------------------------------------------------------------

    async def action(self, operation_id: str, action: dict, observe: bool, settle: float = 0) -> dict:
        def submit(token):
            request = ActionRequest.model_validate(
                {"operation_id": operation_id, "lease_token": token, "action": action}
            )
            return self.phone.action(operation_id, self.owner, token, request.action)

        return await self._mutate(operation_id, submit, observe, settle)

    async def link(
        self, operation_id: str, bundle_id: str, link: str, params: dict, observe: bool, settle: float = 0
    ) -> dict:
        def submit(token):
            request = LinkRequest.model_validate(
                {
                    "operation_id": operation_id,
                    "lease_token": token,
                    "bundle_id": bundle_id,
                    "link": link,
                    "params": params,
                }
            )
            return self.phone.open_link(
                operation_id, self.owner, token, request.bundle_id, request.link, request.params
            )

        return await self._mutate(operation_id, submit, observe, settle)

    async def full_page(self, operation_id: str, options: dict) -> dict:
        """Scroll-and-capture; the images come back with the operation.

        Each page costs a flick, a settle and a capture (about 3 s per page
        with a slow phone link), so the wait scales with the page budget instead of the
        30-second action deadline. Thumbly keeps going if this wait runs out; the operation is
        then returned as DISPATCHED and its images stay fetchable under its id."""
        pages = int(options.get("max_pages", 20))

        def submit(token):
            request = FullPageRequest.model_validate(
                {"operation_id": operation_id, "lease_token": token, "options": options}
            )
            return self.phone.full_page(operation_id, self.owner, token, request.options)

        async def images():
            return await self._run(
                lambda: self.phone.full_page_result(operation_id, self.owner), operation_id, entry=False
            )

        return await self._mutate(operation_id, submit, False, 0, fetch=images, wait=30 + 15 * pages)

    async def setup(self, kind: str, operation_id: str, payload: dict) -> dict:
        """Queue one owner setup step under this caller's lease; returns its record at once."""

        def submit():
            request = SetupRequest.model_validate(
                {"operation_id": operation_id, "lease_token": self.lease, "payload": payload}
            )
            return self.phone.setup(operation_id, self.owner, self.lease, kind, request.payload)

        return await self._run(submit, operation_id)

    async def _mutate(
        self,
        operation_id: str,
        submit: Callable[[str | None], dict],
        observe: bool,
        settle: float = 0,
        *,
        fetch=None,
        wait: float | None = None,
    ) -> dict:
        """Submit the mutation, wait for its outcome, optionally settle, then capture once.

        ``settle`` is the bounded wait between a completed action and its observation, so the
        returned image shows the screen after the transition the action started (an app
        launch, a search) instead of the frame before it. ``timing`` reports where the seconds
        went so slow calls can be attributed to the phone, the settle, the capture or the path
        outside this bridge. ``fetch`` replaces the observation with the operation's own result
        (a full-page capture's images); ``wait`` overrides the outcome deadline."""
        # First action of a task, or the first after the lease lapsed: Thumbly decides ownership.
        try:
            await self.ensure_control()
        except BridgeError as exc:
            exc.rejected = True
            raise
        started = time.monotonic()
        try:
            try:
                # Synchronous up to the journal: a cancellation cannot land between recording the
                # operation and getting its record back.
                result = await self._run(lambda: submit(self.lease), operation_id)
            except BridgeError as exc:
                if exc.status is not None and 400 <= exc.status < 500:
                    # A validation or state error before anything was journaled. An expired token
                    # is dropped and never replayed; the next call acquires again.
                    exc.rejected = True
                    self.forget(exc)
                raise
            if result["state"] in PENDING:
                deadline = self.wait_seconds if wait is None else wait
                await self._run(
                    lambda: self.coordinator.outcome(operation_id, deadline), operation_id, entry=False
                )
                result = await self.operation(operation_id, entry=False)
            timing = {"action_seconds": round(time.monotonic() - started, 3)}
            output = {"operation": result, "timing": timing}
            if result["state"] == "SUCCEEDED" and fetch is not None:
                fetched = await fetch()
                fetched.pop("operation", None)
                output.update(fetched)
            elif result["state"] == "SUCCEEDED" and observe:
                if settle > 0:
                    await anyio.sleep(settle)
                    timing["settle_seconds"] = settle
                capture_started = time.monotonic()
                try:
                    output["observation"] = await self.screenshot(self.observation)
                except BridgeError as exc:
                    # A failed post-action observation must never turn a completed action into a retry.
                    output["observation_error"] = exc.public()
                timing["observation_seconds"] = round(time.monotonic() - capture_started, 3)
            timing["bridge_seconds"] = round(time.monotonic() - started, 3)
            return output
        except BridgeError as exc:
            # A failure after the submit keeps the lookup key.
            if exc.operation_id is None:
                exc.operation_id = operation_id
            raise
        except asyncio.CancelledError:
            # MCP request cancellation must reach hardware even inside an AnyIO cancel scope.
            with anyio.CancelScope(shield=True), anyio.move_on_after(5):
                with contextlib.suppress(BridgeError):
                    await self.control("pause", str(uuid4()))
                with contextlib.suppress(BridgeError):
                    await self._control("cancel", str(uuid4()), operation_id)
            raise

    async def close(self):
        """Release this caller's lease, then refuse every later call."""
        with anyio.CancelScope(shield=True), anyio.move_on_after(5):
            if self.lease and not self.closed:
                with contextlib.suppress(BridgeError):
                    await self._control("release", str(uuid4()))
        self.lease = None
        self.closed = True
