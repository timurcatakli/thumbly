"""Owner API for the Thumbly Mac app: http://127.0.0.1:<dashboard_port>/api/*.

The listener is bound to loopback, so whoever uses this Mac is the owner; there is no password.
Two guards keep other origins out:

- the Host header must name this listener (127.0.0.1 or localhost), which defeats DNS rebinding;
- every write carries the ``x-thumbly-client: dashboard`` header, which a web page cannot send to
  another origin without a CORS preflight this server never answers.
"""

import ipaddress

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from thumbly.domain.errors import ThumblyError

CLIENT_HEADER = "x-thumbly-client"
DASHBOARD_CLIENT = "dashboard"
DASHBOARD_IDENTITY = "Thumbly app"  # Activity owner label for operations started from the app.


def create_dashboard(service) -> ASGIApp:
    from mcp.server.auth.middleware.auth_context import auth_context_var
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken

    from thumbly.interfaces.app_api import app_routes
    from thumbly.interfaces.mcp_server import ALL_SCOPES
    from thumbly.service import DEVICE_NAME

    port = service.config.dashboard_port
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    def admit(request: Request) -> None:
        """Only this Mac, addressed by a name this listener answers to."""
        client = request.client
        try:
            loopback = client is not None and ipaddress.ip_address(client.host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ThumblyError("THIS_MAC_ONLY", "Use the Thumbly app on this Mac", 403)
        if request.headers.get("host", "").lower() not in hosts:
            raise ThumblyError("HOST_NOT_ALLOWED", "Use http://127.0.0.1", 403)
        if request.method != "GET" and request.headers.get(CLIENT_HEADER, "").lower() != DASHBOARD_CLIENT:
            raise ThumblyError(
                "CLIENT_HEADER_REQUIRED", f"{CLIENT_HEADER}: {DASHBOARD_CLIENT} is required", 403
            )

    def owner(handler):
        """Run the route as the owner's identity after the guards above."""

        async def route(request: Request):
            try:
                admit(request)
            except ThumblyError as exc:
                return JSONResponse({"error": exc.as_dict()}, status_code=exc.status)
            user = AuthenticatedUser(
                AccessToken(token="dashboard", client_id=DASHBOARD_IDENTITY, scopes=list(ALL_SCOPES))
            )
            context = auth_context_var.set(user)
            try:
                return await handler(request)
            finally:
                auth_context_var.reset(context)

        return route

    async def overview(_request):
        """First-run facts the app needs before anything is bound."""
        pairing = service.autopair.status()
        if pairing.get("last_error"):
            pairing["last_error"] = {"code": pairing["last_error"].get("code")}
        phones = await service.phone.usb_phones()
        return JSONResponse(
            {
                "name": DEVICE_NAME,
                "bound": service.bindings.read() is not None,
                "cable": len(phones) == 1,
                "usb_pairing": pairing,
            }
        )

    routes = [Route("/api/overview", owner(overview))]
    routes += [
        Route(path, owner(handler), methods=methods)
        for path, methods, handler in app_routes(service, service.gateway, service.auth_provider)
    ]

    class Headers:
        def __init__(self, app: ASGIApp):
            self.app = app

        async def __call__(self, scope: Scope, receive: Receive, send: Send):
            async def secure_send(message):
                if message["type"] == "http.response.start":
                    message["headers"] = [
                        *message.get("headers", []),
                        (b"cache-control", b"no-store"),
                        (b"x-content-type-options", b"nosniff"),
                    ]
                await send(message)

            await self.app(scope, receive, secure_send)

    return Headers(Starlette(routes=routes))
