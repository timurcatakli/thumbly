"""OAuth device authorization grant (RFC 8628) for scripts and other clients without a browser.

A script asks ``/oauth/device_authorization`` for a device code. The request lands in the same
pending list the dashboard shows under Access, and the owner approves it there exactly like an
MCP client, choosing the same windows. The script polls ``/token`` with the device code until the
decision arrives; the token pair it receives is an ordinary grant of this issuer, refreshed with
the standard refresh grant. No code is typed anywhere: the user code is only shown on both sides
so the owner can match the request to the script that made it.

The MCP SDK's token endpoint knows only its own grant types, so ``DeviceGrantRoutes`` sits in
front of it: it answers the device endpoint and the device-code grant, passes every other token
request through unchanged, and adds the device grant to the authorization server metadata.
"""

import json
import re
import secrets
import time
from typing import Any
from urllib.parse import parse_qs
from uuid import uuid4

from mcp.server.auth.provider import RegistrationError
from mcp.shared.auth import OAuthClientInformationFull
from starlette.responses import JSONResponse

from thumbly.interfaces.agent_gateway import (
    NEVER_SECONDS,
    REFRESH_SECONDS,
    REGISTRATIONS_PER_MINUTE,
    approve_link,
)
from thumbly.interfaces.auth import digest
from thumbly.interfaces.mcp_server import SCOPES

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
DEVICE_PATH = "/oauth/device_authorization"
TOKEN_PATH = "/token"
METADATA_PATH = "/.well-known/oauth-authorization-server"
DEVICE_SECONDS = 10 * 60  # A pending device request, like every pending consent.
INTERVAL = 5  # Seconds between polls (RFC 8628 default); slow_down adds 5 up to MAX_INTERVAL.
MAX_INTERVAL = 30
DEVICE_PENDING = 5  # Pending device requests at once.
POLLS = 150  # Polls one device code may make; enough for its lifetime at the base interval.
FAILED_POLLS_PER_MINUTE = 60  # Unknown or mismatched device codes, service-wide.
FORM_LIMIT = 4096
FORM_FIELDS = 16
CODE_MAX = 256
NAME_MAX = 64
# RFC 8628 §6.1: consonants only, so no word can form and no character looks like another.
USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"


def user_code() -> str:
    raw = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def oauth_error(error: str, description: str | None = None, status: int = 400) -> JSONResponse:
    body = {"error": error}
    if description:
        body["error_description"] = description
    return JSONResponse(body, status_code=status, headers={"Pragma": "no-cache"})


def parse_form(data: bytes) -> dict[str, str] | None:
    """A small urlencoded form, or None when it has more than FORM_FIELDS fields."""
    try:
        parsed = parse_qs(data.decode("utf-8", "replace"), keep_blank_values=True, max_num_fields=FORM_FIELDS)
    except ValueError:
        return None
    return {key: values[0] for key, values in parsed.items()}


def is_device(info: OAuthClientInformationFull) -> bool:
    return DEVICE_GRANT in info.grant_types


def record_decision(provider, handle: str, client_id: str, value: dict, granted: list[str], window: int):
    """Store the owner's decision on a device request for its device code to collect once.

    Called from ``OAuthProvider.decide_handle`` by whichever provider the owner used (the dashboard
    or an admin client), so it is bound to the issuer the script polls, recorded in the request.
    """
    decision: dict[str, Any] = {"granted": granted}
    if granted:
        decision |= {"window": window, "sliding": window == REFRESH_SECONDS, "never": window >= NEVER_SECONDS}
    provider._store(
        "device",
        "device:" + handle,
        client_id,
        handle,
        decision,
        time.time() + DEVICE_SECONDS,
        issuer=value.get("issuer"),
    )


class DeviceGrant:
    """Device requests and their decisions, stored with the provider's other grants."""

    def __init__(self, provider):
        self.provider = provider
        # handle -> [last poll, interval, polls]. Bounded by DEVICE_PENDING live requests plus
        # tombstones of requests that ended less than DEVICE_SECONDS ago.
        self.polls: dict[str, list[float]] = {}
        self.failed: list[float] = []

    # -- device authorization request -----------------------------------------------------

    async def authorize(self, form: dict[str, str]) -> JSONResponse:
        provider = self.provider
        name = re.sub(r"[\x00-\x1f\x7f]", "", form.get("client_name", "")).strip()
        if not name or len(name) > NAME_MAX:
            return oauth_error("invalid_request", f"client_name is required, at most {NAME_MAX} characters")
        requested = form.get("scope", "").split()
        # The owner-administration scope is never offered to a device client, on any entry.
        if any(scope not in SCOPES for scope in requested):
            return oauth_error("invalid_scope", "a device client may ask for phone:read and phone:control")
        scopes = list(SCOPES)
        now = time.time()
        provider.prune(now)
        self._prune(now)
        if self._pending_count(now) >= DEVICE_PENDING:
            return oauth_error("slow_down", "too many pending device requests; retry later", 429)
        client = await self._client(form.get("client_id", ""), name)
        if client is None:
            info = OAuthClientInformationFull(
                client_id=str(uuid4()),
                client_id_issued_at=int(now),
                client_name=name,
                redirect_uris=None,
                grant_types=[DEVICE_GRANT, "refresh_token"],
                token_endpoint_auth_method="none",
                scope=" ".join(scopes),
            )
            try:
                await provider.register_client(info)
            except RegistrationError as exc:
                return oauth_error("slow_down", exc.error_description, 429)
            client = info
        else:
            # A known client still spends the registration budget, so re-requests stay bounded.
            provider.registrations = [t for t in provider.registrations if t > now - 60]
            if len(provider.registrations) >= REGISTRATIONS_PER_MINUTE:
                return oauth_error("slow_down", "registration rate limit; retry later", 429)
            provider.registrations.append(now)
        # One pending request per client: a new one replaces the old.
        with provider.db:
            provider.db.execute(
                "DELETE FROM oauth_grants WHERE kind='request' AND client_id=?", (client.client_id,)
            )
        device_code, code = secrets.token_urlsafe(32), user_code()
        handle = digest(device_code)
        provider._store(
            "request",
            device_code,
            client.client_id,
            handle,
            {"client_id": client.client_id, "scopes": scopes, "device": True, "user_code": code},
            now + DEVICE_SECONDS,
        )
        self.polls[handle] = [0.0, INTERVAL, 0]
        link = approve_link(handle)
        return JSONResponse(
            {
                "device_code": device_code,
                "user_code": code,
                "verification_uri": link,
                "verification_uri_complete": link,
                "expires_in": DEVICE_SECONDS,
                "interval": INTERVAL,
                # Not in RFC 8628: the client registered here, which refreshes need.
                "client_id": client.client_id,
            },
            headers={"Pragma": "no-cache"},
        )

    async def _client(self, client_id: str, name: str) -> OAuthClientInformationFull | None:
        """The caller's cached device client of this issuer, when it asks under the same name.

        A request never renames a client: a different name gets a new entry under Access, so
        a denied request cannot relabel a script the owner already approved."""
        if not client_id or len(client_id) > 64:
            return None
        info = await self.provider.get_client(client_id)
        if info is None or not is_device(info) or info.client_name != name:
            return None
        return info

    def _pending_count(self, now: float) -> int:
        rows = self.provider.db.execute(
            "SELECT value FROM oauth_grants WHERE kind='request' AND revoked=0 AND expires>?", (now,)
        ).fetchall()
        return sum(1 for row in rows if json.loads(row["value"]).get("device"))

    def _prune(self, now: float) -> None:
        live = {
            row["digest"]
            for row in self.provider.db.execute(
                "SELECT digest FROM oauth_grants WHERE kind='request' AND expires>?", (now,)
            ).fetchall()
        }
        for handle, state in list(self.polls.items()):
            # Keep a tombstone for a lifetime so a late poll learns expired_token, not invalid_grant.
            if handle not in live and now - state[0] > DEVICE_SECONDS:
                del self.polls[handle]
        self.failed = [t for t in self.failed if t > now - 60]

    # -- device code grant at /token -------------------------------------------------------

    async def token(self, form: dict[str, str]) -> JSONResponse:
        provider = self.provider
        now = time.time()
        self._prune(now)
        device_code, client_id = form.get("device_code", ""), form.get("client_id", "")
        if not device_code or len(device_code) > CODE_MAX or not client_id:
            return self._failed(now, oauth_error("invalid_request", "device_code and client_id are required"))
        handle = digest(device_code)
        state = self.polls.get(handle)
        key = digest("device:" + handle)
        decided = provider._grant("device", "device:" + handle, now)
        if decided is not None and not decided["revoked"] and decided["client_id"] == client_id:
            with provider.db:
                taken = provider.db.execute("DELETE FROM oauth_grants WHERE digest=?", (key,)).rowcount
            if taken != 1:  # Another poll collected it first: device codes are single-use.
                return oauth_error("invalid_grant", "device code already used")
            self.polls.pop(handle, None)
            value = json.loads(decided["value"])
            if not value["granted"]:
                return oauth_error("access_denied", "the owner denied this request")
            family = secrets.token_hex(16)
            issued = provider._issue(
                client_id,
                value["granted"],
                family,
                now + value["window"],
                now,
                value["sliding"],
                value["never"],
            )
            return JSONResponse(issued.model_dump(exclude_none=True), headers={"Pragma": "no-cache"})
        pending = provider.pending_request(device_code)
        if pending is None or pending[0].client_id != client_id or not pending[1].get("device"):
            if state is not None:
                return self._failed(now, oauth_error("expired_token", "the request expired; start again"))
            return self._failed(now, oauth_error("invalid_grant", "unknown device code"))
        if state is None:  # Thumbly restarted while the request was pending.
            state = self.polls[handle] = [0.0, INTERVAL, 0]
        last, interval, polls = state
        state[0], state[2] = now, polls + 1
        if polls + 1 > POLLS:
            with provider.db:
                provider.db.execute("DELETE FROM oauth_grants WHERE digest=?", (handle,))
            return oauth_error("expired_token", "too many polls; start again")
        if now - last < interval:
            state[1] = min(interval + 5, MAX_INTERVAL)
            return oauth_error("slow_down", f"poll at most every {int(state[1])} seconds")
        return oauth_error("authorization_pending", "waiting for the owner in the Thumbly app")

    def _failed(self, now: float, response: JSONResponse) -> JSONResponse:
        """Unknown, malformed or mismatched codes share a service-wide budget. Approved and pending
        codes are answered before it, so junk cannot block a real script's decision."""
        if len(self.failed) >= FAILED_POLLS_PER_MINUTE:
            return oauth_error("slow_down", "too many failed polls; retry later", 429)
        self.failed.append(now)
        return response


class DeviceGrantRoutes:
    """ASGI layer in front of the MCP app: device endpoint, device-code grant, metadata."""

    def __init__(self, app, grant: DeviceGrant):
        self.app, self.grant = app, grant

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope.get("path"), scope.get("method")
        if path == DEVICE_PATH and method == "POST":
            data = await self._body(receive)
            form = parse_form(data) if data is not None else None
            if data is None:
                response = oauth_error("invalid_request", "request too large", 413)
            elif form is None:
                response = oauth_error("invalid_request", "too many form fields")
            else:
                response = await self.grant.authorize(form)
            return await response(scope, receive, send)
        if path == TOKEN_PATH and method == "POST":
            data = await self._body(receive)
            if data is None:
                return await oauth_error("invalid_request", "request too large", 413)(scope, receive, send)
            form = parse_form(data)
            if form is None:
                return await oauth_error("invalid_request", "too many form fields")(scope, receive, send)
            if form.get("grant_type") == DEVICE_GRANT:
                return await (await self.grant.token(form))(scope, receive, send)
            return await self.app(scope, self._replay(data, receive), send)
        if path == METADATA_PATH and method == "GET":
            return await self._metadata(scope, receive, send)
        return await self.app(scope, receive, send)

    @staticmethod
    async def _body(receive) -> bytes | None:
        data = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None
            data.extend(message.get("body", b""))
            if len(data) > FORM_LIMIT:
                return None
            if not message.get("more_body"):
                return bytes(data)

    @staticmethod
    def _replay(data: bytes, receive):
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": data, "more_body": False}
            return await receive()

        return replay

    async def _metadata(self, scope, receive, send):
        """Advertise the device endpoint and grant next to what the MCP SDK publishes."""
        start: dict = {}
        body = bytearray()

        async def capture(message):
            if message["type"] == "http.response.start":
                start.update(message)
            elif message["type"] == "http.response.body":
                body.extend(message.get("body", b""))

        await self.app(scope, receive, capture)
        try:
            if start.get("status") != 200:
                raise ValueError
            metadata = json.loads(body)
            metadata["device_authorization_endpoint"] = self.grant.provider.issuer + DEVICE_PATH
            grants = metadata.setdefault("grant_types_supported", [])
            if DEVICE_GRANT not in grants:
                grants.append(DEVICE_GRANT)
            payload = json.dumps(metadata).encode()
            headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
            start["headers"] = [*headers, (b"content-length", str(len(payload)).encode())]
        except ValueError:
            payload = bytes(body)
        await send(start)
        await send({"type": "http.response.body", "body": payload})
