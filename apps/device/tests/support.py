import asyncio
import base64
import contextlib
import hashlib
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from thumbly.config import Config
from thumbly.domain.errors import ThumblyError


@contextlib.contextmanager
def raises_code(code: str):
    """Assert an ThumblyError by its stable code rather than its human message."""
    with pytest.raises(ThumblyError) as info:
        yield info
    assert info.value.code == code, info.value.code


# -- OAuth helpers parameterized by issuer (the remote entry and the local entry share one Runtime) --

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PUBLIC = "https://my-mac.tailnet.ts.net"
LOCAL = "http://127.0.0.1:8771"
DASHBOARD = "http://127.0.0.1:8770"
APP_CALLBACK = "https://agent.example/oauth/callback"  # A client of the local entry.
CONSENT_PATH = "/oauth/consent"


def make_config(tmp_path, **overrides):
    return Config(
        backend="simulated",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "run",
        public_url=PUBLIC,
        **overrides,
    )


def web(app, origin, **kwargs):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=origin, **kwargs)


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


async def register(c, *, callback=APP_CALLBACK, scope="phone:read phone:control", name="Thumbly App"):
    metadata = {"client_name": name, "redirect_uris": [callback], "token_endpoint_auth_method": "none"}
    if scope is not None:
        metadata["scope"] = scope
    return await c.post("/register", json=metadata)


async def start_authorization(c, origin, client_id, *, callback=APP_CALLBACK, scope=None, state="abc"):
    """GET /authorize; return (response, request_id or None, verifier)."""
    verifier, challenge = pkce()
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": callback,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "resource": origin + "/mcp",
    }
    if scope is not None:
        params["scope"] = scope
    r = await c.get("/authorize", params=params)
    request_id = None
    if r.status_code == 302 and r.headers["location"].startswith(origin + CONSENT_PATH + "?request="):
        request_id = parse_qs(urlsplit(r.headers["location"]).query)["request"][0]
    return r, request_id, verifier


def owner_approve(service, request_id, *, window="30d") -> str:
    """The owner clicks Approve in the Thumbly app; return the client redirect the consent page polls for."""
    from thumbly.interfaces.agent_gateway import grant_from_choice
    from thumbly.interfaces.auth import digest

    # Like /app/consents/{handle}: any entry's request, addressed by its handle.
    provider, handle = service.auth_provider, digest(request_id)
    pending = provider.pending_by_handle(handle)
    assert pending is not None, "request expired"
    granted, seconds = grant_from_choice(pending[1]["scopes"], window)
    return provider.decide_handle(handle, granted, seconds)


async def tokens(c, origin, client_id, code, verifier, *, callback=APP_CALLBACK):
    return await c.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": client_id,
            "redirect_uri": callback,
            "resource": origin + "/mcp",
        },
    )


async def local_token(c, service, *, window="30d", name="Thumbly App"):
    """Register, authorize and approve a client on the local entry; return (client_id, token response)."""
    registered = await register(c, name=name)
    assert registered.status_code == 201, registered.text
    client_id = registered.json()["client_id"]
    _, request_id, verifier = await start_authorization(c, LOCAL, client_id)
    assert request_id
    code = parse_qs(urlsplit(owner_approve(service, request_id, window=window)).query)["code"][0]
    issued = await tokens(c, LOCAL, client_id, code, verifier)
    assert issued.status_code == 200, issued.text
    return client_id, issued.json()


THIS_MAC = ("127.0.0.1", 50000)


@contextlib.asynccontextmanager
async def dashboard(app, *, client=THIS_MAC):
    """The dashboard as a browser on this Mac sees it."""
    from thumbly.interfaces.dashboard import CLIENT_HEADER, DASHBOARD_CLIENT

    transport = httpx.ASGITransport(app=app, client=client)
    async with httpx.AsyncClient(
        transport=transport, base_url=DASHBOARD, headers={CLIENT_HEADER: DASHBOARD_CLIENT}
    ) as c:
        yield c


async def narrow(c, origin, client_id, issued: dict, *, drop: str):
    """Refresh with a smaller scope: the OAuth way to hold a read-only token."""
    scope = " ".join(s for s in issued["scope"].split() if s != drop)
    return await c.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": issued["refresh_token"],
            "client_id": client_id,
            "scope": scope,
            "resource": origin + "/mcp",
        },
    )


async def lifespan(app, ready, stop):
    """ASGITransport never runs lifespan; the session manager and gateway sweeper need it."""
    pending = [{"type": "lifespan.startup"}]

    async def receive():
        if pending:
            return pending.pop()
        await stop.wait()
        return {"type": "lifespan.shutdown"}

    async def send(message):
        if message["type"].endswith(".failed"):
            raise RuntimeError(message)
        if message["type"] == "lifespan.startup.complete":
            ready.set()

    await app({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)


@contextlib.asynccontextmanager
async def running(app):
    ready, stop = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(lifespan(app, ready, stop))
    await ready.wait()
    try:
        yield app
    finally:
        stop.set()
        await task
