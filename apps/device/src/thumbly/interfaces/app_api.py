"""Owner JSON routes (``/api/*``).

Served by the dashboard only, which runs them as its own identity. Phone operations go through
the same gateway bridge the MCP tools use, so leases, journaling, pause and rate limits apply
identically whichever way a request arrived.
"""

import contextlib
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from thumbly.domain.errors import ThumblyError
from thumbly.interfaces.json_body import body
from thumbly.interfaces.mcp_server import ADMIN_SCOPE, ALL_SCOPES, CONTROL_SCOPE, READ_SCOPE
from thumbly.interfaces.phone_channel import LOST_CONTROL, BridgeError

APP_PREFIX = "/api"
# validate_shortcut records the owner's evidence that a shortcut works on this phone.
SETUP_KINDS = {
    "bind",
    "transport",
    "recover",
    "pair",
    "developer_mode",
    "mount_ddi",
    "unbind",
    "validate_shortcut",
}

Handler = Callable[[Request], Awaitable[Response]]
RouteSpec = tuple[str, list[str], Handler]


def require(scope: str | None) -> AccessToken:
    token = get_access_token()
    if token is None:
        raise ThumblyError("LOGIN_REQUIRED", status=401)
    if scope is not None and scope not in token.scopes:
        raise ThumblyError("SCOPE_REQUIRED", f"{scope} not granted", 403)
    return token


def guarded(handler: Callable[[Request], Awaitable[Any]]) -> Handler:
    """Wrap a coroutine returning JSON data or a Response; map errors to the shared envelope."""

    async def route(request: Request) -> Response:
        try:
            result = await handler(request)
        except BridgeError as exc:
            # Phone core errors keep their status; gateway-level ones map by code.
            status = exc.status or {"LOGIN_REQUIRED": 401, "SCOPE_REQUIRED": 403, "RATE_LIMITED": 429}.get(
                exc.code, 409
            )
            return JSONResponse({"error": exc.public()}, status_code=status)
        except ThumblyError as exc:
            return JSONResponse({"error": exc.as_dict()}, status_code=exc.status)
        except ValueError:
            return JSONResponse(
                {"error": {"code": "INVALID_REQUEST", "message": "Request fields are invalid"}},
                status_code=400,
            )
        if isinstance(result, Response):
            return result
        return JSONResponse(result)

    return route


def app_routes(service, gateway, provider) -> list[RouteSpec]:
    """Every ``/api/*`` route as (path, methods, guarded handler)."""
    from thumbly import __version__
    from thumbly.interfaces.agent_gateway import grant_from_choice, owner_of
    from thumbly.service import DEVICE_NAME

    config = service.config
    routes: list[RouteSpec] = []

    def add(path: str, methods: list[str]):
        def decorator(fn):
            routes.append((APP_PREFIX + path, methods, guarded(fn)))
            return fn

        return decorator

    async def operation_body(request: Request) -> dict:
        data = await body(request)
        operation_id = data.get("operation_id") or str(uuid4())
        if not isinstance(operation_id, str) or len(operation_id) > 64:
            raise ThumblyError("INVALID_OPERATION_ID", status=400)
        data["operation_id"] = operation_id
        return data

    @add("/info", ["GET"])
    async def info(request: Request):
        return {
            "name": DEVICE_NAME,
            "version": __version__,
            "issuer": provider.issuer,
            "mcp_url": f"http://127.0.0.1:{config.local_mcp_port}/mcp" if config.local_mcp_port else None,
            "public_url": f"{config.public_url}/mcp" if config.public_url else None,
            "scopes": ALL_SCOPES,
        }

    @add("/status", ["GET"])
    async def status(request: Request):
        client = await gateway.bridge(READ_SCOPE)
        result = await client.status()
        result["pending_consents"] = len(provider.list_pending())
        return result

    @add("/capabilities", ["GET"])
    async def capabilities(request: Request):
        client = await gateway.bridge(READ_SCOPE)
        return await client.capabilities()

    @add("/pause", ["POST"])
    async def pause(request: Request):
        client = await gateway.bridge(READ_SCOPE, exempt=True)
        data = await operation_body(request)
        return await client.control("pause", data["operation_id"])

    @add("/resume", ["POST"])
    async def resume(request: Request):
        """The owner lifts a pause: take control, resume, hand control back."""
        client = await gateway.bridge(READ_SCOPE, exempt=True)
        data = await operation_body(request)
        result = await client.control("resume", data["operation_id"])
        with contextlib.suppress(BridgeError):
            await client.control("release", str(uuid4()))
        return result

    @add("/operations", ["GET"])
    async def operations(request: Request):
        """Journal page of summaries with a ``(created, id)`` cursor; ``before`` is the older
        timestamp-only form, accepted for one release."""
        require(ADMIN_SCOPE)
        limit = int(request.query_params.get("limit", "20"))
        cursor = request.query_params.get("cursor")
        before = request.query_params.get("before")
        if cursor is None and before:
            cursor = f"{float(before)!r}:"
        actions_only = request.query_params.get("actions") == "1"
        return service.journal.page_operations(limit, cursor, actions_only=actions_only)

    @add("/operations/clear", ["POST"])
    async def clear_operations(request: Request):
        require(ADMIN_SCOPE)
        return {"cleared": service.journal.clear_operations()}

    @add("/operations/{id}", ["GET"])
    async def operation(request: Request):
        client = await gateway.bridge(READ_SCOPE)
        return await client.operation(request.path_params["id"])

    @add("/clients", ["GET"])
    async def clients(request: Request):
        """OAuth clients of every issuer, each flagged when it is the caller."""
        token = require(ADMIN_SCOPE)
        result = provider.list_clients(every_issuer=True)
        for row in result:
            row["kind"] = "oauth"
            row["self"] = row["client_id"] == token.client_id
        return {"clients": result}

    @add("/clients/{id}/revoke", ["POST"])
    async def revoke(request: Request):
        token = require(ADMIN_SCOPE)
        client_id = request.path_params["id"]
        if client_id == token.client_id:
            raise ThumblyError("CANNOT_REVOKE_SELF", "A client cannot revoke its own access", 409)
        if not provider.revoke_client(client_id):
            raise ThumblyError("UNKNOWN_CLIENT", status=404)
        # The client's bridge lives in its entry's gateway; free the phone here at once instead
        # of waiting for that gateway's next sweep.
        lease = service.coordinator.lease
        if lease is not None and lease.owner == owner_of(client_id):
            await service.coordinator.drop_control()
        await gateway.sweep()
        return {"revoked": client_id}

    @add("/consents", ["GET"])
    async def consents(request: Request):
        """Pending AI-client consent requests (any entry), for the owner to decide."""
        require(ADMIN_SCOPE)
        return {"consents": provider.list_pending()}

    @add("/consents/{handle}", ["POST"])
    async def decide_consent(request: Request):
        require(ADMIN_SCOPE)
        handle = request.path_params["handle"]
        data = await body(request)
        pending = provider.pending_by_handle(handle)
        if pending is None:
            raise ThumblyError("CONSENT_EXPIRED", status=404)
        _, value = pending
        if data.get("decision") != "allow":
            provider.decide_handle(handle, None, 0)
            return {"decided": "deny", "scopes": []}
        granted, seconds = grant_from_choice(value["scopes"], str(data.get("window", "")))
        provider.decide_handle(handle, granted, seconds)
        return {"decided": "allow", "scopes": granted, "window": seconds}

    @add("/discover", ["GET"])
    async def discover(request: Request):
        client = await gateway.bridge(ADMIN_SCOPE)
        return await client.discover()

    @add("/setup/{kind}", ["POST"])
    async def setup(request: Request):
        require(ADMIN_SCOPE)
        kind = request.path_params["kind"]
        if kind not in SETUP_KINDS:
            raise ThumblyError("UNKNOWN_SETUP_ACTION", status=404)
        client = await gateway.bridge(CONTROL_SCOPE)
        data = await operation_body(request)
        for attempt in range(2):
            await client.ensure_control()
            try:
                result = await client.setup(kind, data["operation_id"], data.get("payload", {}))
                break
            except BridgeError as exc:
                # A token that lapsed since the page's last step is refused before anything is
                # journaled, so taking control again and sending once more cannot run it twice.
                client.forget(exc)
                if attempt or exc.code not in LOST_CONTROL:
                    raise
        return JSONResponse(result, status_code=202 if result.get("state") == "QUEUED" else 200)

    @add("/reset", ["POST"])
    async def reset(request: Request):
        """Back to first-time setup. The owner types the name shown in the dialog; the server
        checks it too, so a stray request cannot erase anything."""
        require(ADMIN_SCOPE)
        data = await body(request)
        typed = data.get("name")
        if not isinstance(typed, str) or typed.strip().casefold() != DEVICE_NAME.casefold():
            raise ThumblyError("RESET_NAME_MISMATCH", f"Type {DEVICE_NAME} to confirm", 400)
        if await service.factory_reset():
            return JSONResponse({"state": "reset"})
        return JSONResponse({"state": "resetting"}, status_code=202)

    @add("/diagnostics", ["GET"])
    async def diagnostics(request: Request):
        require(ADMIN_SCOPE)
        from thumbly.application.diagnostics import export

        return await export(service)

    return routes


def redact(text: str) -> str:
    """Drop token-shaped runs and phone identifiers from log excerpts."""
    import re

    text = re.sub(r"[A-Za-z0-9_-]{32,}", "<redacted>", text)
    text = re.sub(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}\b", "<udid>", text)
    return text
