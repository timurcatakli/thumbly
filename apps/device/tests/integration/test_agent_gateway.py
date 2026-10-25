import asyncio
import base64
import hashlib
import secrets
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from support import narrow, owner_approve

from thumbly.config import Config
from thumbly.interfaces import agent_gateway
from thumbly.interfaces.agent_gateway import CONSENT_PATH, STATUS_PATH, TIMING_LOG, create_gateway_app
from thumbly.service import Service

PUBLIC = "https://my-mac.tailnet.ts.net"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture
async def remote(tmp_path):
    config = Config(
        backend="simulated",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "run",
        public_url=PUBLIC,
    )
    service = Service(config)
    await service.start()
    app = create_gateway_app(service)
    ready, stop = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(lifespan(app, ready, stop))
    await ready.wait()
    yield service, app
    stop.set()
    await task
    await service.close()


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


def web(app, **kwargs):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC, **kwargs)


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


async def register(c, scope="phone:read phone:control"):
    metadata = {"client_name": "Claude", "redirect_uris": [CALLBACK], "token_endpoint_auth_method": "none"}
    if scope is not None:
        metadata["scope"] = scope
    r = await c.post("/register", json=metadata)
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


async def authorize(c, service, client_id, *, window="24h", scope="phone:read phone:control"):
    verifier, challenge = pkce()
    r = await c.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": CALLBACK,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "abc",
            "scope": scope,
            "resource": PUBLIC + "/mcp",
        },
    )
    assert r.status_code == 302, r.text
    consent = r.headers["location"]
    assert consent.startswith(PUBLIC + CONSENT_PATH + "?request=")
    page = await c.get(consent)
    assert page.status_code == 200 and "Claude" in page.text and "claude.ai" in page.text
    request_id = parse_qs(urlsplit(consent).query)["request"][0]
    assert "name='control'" not in page.text and "name='window'" not in page.text
    redirect = owner_approve(service, request_id, window=window)
    assert (await c.get(STATUS_PATH, params={"request": request_id})).json() == {
        "state": "decided",
        "redirect": redirect,
    }
    query = parse_qs(urlsplit(redirect).query)
    assert redirect.startswith(CALLBACK) and query["state"] == ["abc"]
    return query["code"][0], verifier


async def tokens(c, client_id, code, verifier):
    r = await c.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": client_id,
            "redirect_uri": CALLBACK,
            "resource": PUBLIC + "/mcp",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


async def connect(app, access):
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url=PUBLIC,
        headers={"Authorization": f"Bearer {access}"},
    )
    return Client(streamable_http_client(PUBLIC + "/mcp", http_client=http)), http


async def test_discovery_registration_consent_and_tool_scopes(remote):
    service, app = remote
    async with web(app) as c:
        prm = await c.get("/.well-known/oauth-protected-resource/mcp")
        assert prm.status_code == 200 and prm.json()["authorization_servers"] == [PUBLIC]
        meta = (await c.get("/.well-known/oauth-authorization-server")).json()
        assert meta["code_challenge_methods_supported"] == ["S256"]
        assert meta["registration_endpoint"] == PUBLIC + "/register"
        unauthenticated = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
        assert unauthenticated.status_code == 401
        assert "oauth-protected-resource" in unauthenticated.headers["www-authenticate"]
        assert unauthenticated.headers["cache-control"] == "no-store"
        assert unauthenticated.headers["referrer-policy"] == "same-origin"
        assert (
            await c.get("/.well-known/oauth-authorization-server", headers={"Host": "evil.test"})
        ).status_code == 403

        client_id = await register(c)
        code, verifier = await authorize(c, service, client_id)
        issued = await tokens(c, client_id, code, verifier)
        # Approval grants the screen and input together; the client may narrow on refresh.
        assert issued["scope"] == "phone:read phone:control" and issued["expires_in"] <= 900
        reused = await c.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "client_id": client_id,
                "redirect_uri": CALLBACK,
            },
        )
        assert reused.status_code == 400 and reused.json()["error"] == "invalid_grant"
        narrowed = await narrow(c, PUBLIC, client_id, issued, drop="phone:control")
        assert narrowed.status_code == 200 and narrowed.json()["scope"] == "phone:read"

    client, http = await connect(app, narrowed.json()["access_token"])
    async with http, client:
        status = await client.call_tool("thumbly_status", {})
        assert not status.is_error and status.structured_content["mcp"]["transport"] == "streamable-http"
        shot = await client.call_tool("thumbly_screenshot", {})
        assert not shot.is_error
        image = next(b for b in shot.content if b.type == "image")
        assert base64.b64decode(image.data).startswith(b"\xff\xd8")
        denied = await client.call_tool(
            "thumbly_control", {"command": "acquire", "operation_id": str(uuid4())}
        )
        assert denied.is_error and denied.structured_content["error"]["code"] == "SCOPE_REQUIRED"
        assert (await client.call_tool("thumbly_pause", {"operation_id": str(uuid4())})).is_error is False
    assert service.coordinator.lease is None


async def test_control_scope_flow_rotation_reuse_and_revocation(remote, caplog):
    service, app = remote
    TIMING_LOG.addHandler(caplog.handler)  # the entry logger does not propagate to the root logger
    async with web(app) as c:
        client_id = await register(c)
        code, verifier = await authorize(c, service, client_id, window="24h")
        issued = await tokens(c, client_id, code, verifier)
        assert issued["scope"] == "phone:read phone:control"

        client, http = await connect(app, issued["access_token"])
        async with http, client:
            # No explicit acquire: the first action takes the lease for this OAuth client.
            assert service.coordinator.lease is None
            shot = await client.call_tool("thumbly_screenshot", {"format": "jpeg", "max_size": 512})
            frame = shot.structured_content["metadata"]
            tapped = await client.call_tool(
                "thumbly_tap",
                {
                    "operation_id": str(uuid4()),
                    "frame_id": frame["frame_id"],
                    "geometry_epoch": frame["geometry_epoch"],
                    "x": 0.5,
                    "y": 0.5,
                },
            )
            assert not tapped.is_error and tapped.structured_content["operation"]["state"] == "SUCCEEDED"
            assert service.coordinator.lease is not None
            assert "lease_token" not in str(tapped.structured_content)
            timed = [r.getMessage() for r in caplog.records if r.name == "thumbly.mcp.entry"]
            assert any(m.startswith("tools/call thumbly_tap ") and "status=200" in m for m in timed), timed
            assert not any("frame_id" in m or "0.5" in m for m in timed)
            assert service.coordinator.lease is not None

        refreshed = await c.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": issued["refresh_token"],
                "client_id": client_id,
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        fresh = refreshed.json()
        assert fresh["access_token"] != issued["access_token"]

        # A lost reply: the retired token comes back within the grace period and rotates again,
        # once. The pair the client never saw is retired.
        def refresh(token):
            return c.post(
                "/token",
                data={"grant_type": "refresh_token", "refresh_token": token, "client_id": client_id},
            )

        retried = await refresh(issued["refresh_token"])
        assert retried.status_code == 200, retried.text
        second = retried.json()
        assert second["refresh_token"] not in {fresh["refresh_token"], issued["refresh_token"]}
        assert await app.provider.load_access_token(fresh["access_token"]) is None
        assert await app.provider.load_access_token(second["access_token"]) is not None
        # A second reuse is theft, not a lost reply: the whole family ends.
        replay = await refresh(issued["refresh_token"])
        assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
        client, http = await connect(app, second["access_token"])
        async with http:
            with pytest.raises(Exception):
                async with client:
                    await client.call_tool("thumbly_status", {})

        # Approve again, then revoke from the CLI path: the sweeper drops the lease.
        code, verifier = await authorize(c, service, client_id)
        again = await tokens(c, client_id, code, verifier)
        client, http = await connect(app, again["access_token"])
        async with http, client:
            acquired = await client.call_tool(
                "thumbly_control", {"command": "acquire", "operation_id": str(uuid4())}
            )
            assert not acquired.is_error
            assert app.provider.revoke_client(client_id)
            await app.gateway.sweep()
            assert service.coordinator.lease is None
            # The bearer token is dead at the middleware: 401 before any tool runs.
            with pytest.raises(Exception):
                await client.call_tool("thumbly_status", {})
        assert app.provider.list_clients() == []


async def test_pause_is_never_rate_limited(remote, monkeypatch):
    service, app = remote
    monkeypatch.setattr(agent_gateway, "CALLS_PER_MINUTE", 3)
    async with web(app) as c:
        client_id = await register(c)
        code, verifier = await authorize(c, service, client_id)
        issued = await tokens(c, client_id, code, verifier)
    client, http = await connect(app, issued["access_token"])
    async with http, client:
        for _ in range(3):
            assert not (await client.call_tool("thumbly_status", {})).is_error
        limited = await client.call_tool("thumbly_status", {})
        assert limited.is_error and limited.structured_content["error"]["code"] == "RATE_LIMITED"
        for _ in range(3):
            paused = await client.call_tool("thumbly_pause", {})
            assert not paused.is_error, paused.structured_content
        assert service.coordinator.paused


async def test_a_client_keeps_its_owner_across_bridge_drops(remote):
    """The owner is the client, not a session: after its bridge is dropped (a restart, the old
    8-hour session end) the same client still reads its own operations, under one label."""
    service, app = remote
    async with web(app) as c:
        client_id = await register(c)
        code, verifier = await authorize(c, service, client_id)
        issued = await tokens(c, client_id, code, verifier)
    client, http = await connect(app, issued["access_token"])
    async with http, client:
        op = str(uuid4())
        pressed = await client.call_tool(
            "thumbly_key", {"key": "enter", "operation_id": op, "observe": False}
        )
        assert not pressed.is_error, pressed.structured_content
        await app.gateway._drop(client_id)
        assert service.coordinator.lease is None
        looked_up = await client.call_tool("thumbly_operation_status", {"operation_id": op})
        assert not looked_up.is_error and looked_up.structured_content["state"] == "SUCCEEDED"
    owner = agent_gateway.owner_of(client_id)
    labels = service.journal.db.execute("SELECT label FROM owner_labels WHERE owner=?", (owner,)).fetchall()
    assert owner.startswith("client:") and [tuple(row) for row in labels] == [("Claude",)]


async def test_no_scope_request_offers_registered_scopes(remote):
    service, app = remote
    async with web(app) as c:
        client_id = await register(c, scope=None)  # registration falls back to default scopes
        _, challenge = pkce()
        started = await c.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": CALLBACK,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        page = await c.get(started.headers["location"])
        assert "touch, type and press buttons" in page.text and "name='window'" not in page.text
        # A client registered for read only (Claude copies the 401 challenge's scope) is still
        # offered control; the owner decides.
        read_only = await register(c, scope="phone:read")
        started = await c.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": read_only,
                "redirect_uri": CALLBACK,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "phone:read",
            },
        )
        page = await c.get(started.headers["location"])
        assert "touch, type and press buttons" in page.text
        # A client that only asks for read still gets control offered; the owner decides.
        started = await c.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": CALLBACK,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "phone:read",
            },
        )
        page = await c.get(started.headers["location"])
        assert "touch, type and press buttons" in page.text


async def test_consent_rejections_and_pkce_downgrade(remote):
    service, app = remote
    async with web(app) as c:
        client_id = await register(c)
        _, challenge = pkce()
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": CALLBACK,
            "code_challenge": challenge,
            "state": "s",
        }
        plain = await c.get("/authorize", params={**params, "code_challenge_method": "plain"})
        assert plain.status_code in {302, 400}
        if plain.status_code == 302:
            assert "error=" in plain.headers["location"]
        wrong_target = await c.get(
            "/authorize",
            params={**params, "code_challenge_method": "S256", "resource": "https://other.test/mcp"},
        )
        assert "error=invalid_target" in wrong_target.headers["location"]
        started = await c.get("/authorize", params={**params, "code_challenge_method": "S256"})
        request_id = parse_qs(urlsplit(started.headers["location"]).query)["request"][0]
        cross_site = await c.post(
            CONSENT_PATH,
            data={"request": request_id, "decision": "deny"},
            headers={"Origin": "https://evil.test"},
        )
        assert cross_site.status_code == 403
        # The page cannot approve: only the dashboard can. Old credential forms are refused.
        for form in (
            {"request": request_id, "credential": "x", "decision": "allow"},
            {"request": request_id, "pin": "123456", "decision": "pin"},
        ):
            refused = await c.post(CONSENT_PATH, data=form, headers={"Origin": PUBLIC})
            assert refused.status_code == 400 and "Approve this request in the Thumbly app." in refused.text
        assert (await c.get(STATUS_PATH, params={"request": request_id})).json() == {"state": "pending"}
        denied = await c.post(
            CONSENT_PATH, data={"request": request_id, "decision": "deny"}, headers={"Origin": PUBLIC}
        )
        assert denied.status_code == 302 and "error=access_denied" in denied.headers["location"]
        expired = await c.get(CONSENT_PATH, params={"request": request_id})
        assert expired.status_code == 400
        query_token = await c.post(
            "/mcp?access_token=abc", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}
        )
        assert query_token.status_code == 401


async def test_expired_access_token_and_pending_client_pruning(remote):
    service, app = remote
    async with web(app) as c:
        client_id = await register(c)
        code, verifier = await authorize(c, service, client_id)
        issued = await tokens(c, client_id, code, verifier)
        service.journal.db.execute(
            "UPDATE oauth_grants SET expires=? WHERE kind='access'", (time.time() - 1,)
        )
        service.journal.db.commit()
        client, http = await connect(app, issued["access_token"])
        async with http:
            with pytest.raises(Exception):
                async with client:
                    await client.call_tool("thumbly_status", {})
        pending = await register(c)
        service.journal.db.execute(
            "UPDATE oauth_clients SET created=? WHERE client_id=?", (time.time() - 31 * 86_400, pending)
        )
        service.journal.db.commit()
        listed = {row["client_id"] for row in app.provider.list_clients()}
        assert client_id in listed and pending not in listed  # Unapproved for over a month.


class FakeClock:
    """Stands in for the provider's time module, so grant lifetimes can be stepped through."""

    def __init__(self):
        self.now = time.time()

    def time(self):
        return self.now

    @staticmethod
    def monotonic():
        return time.monotonic()


@pytest.fixture
def clock(monkeypatch):
    from thumbly.interfaces import agent_gateway

    fake = FakeClock()
    monkeypatch.setattr(agent_gateway, "time", fake)
    return fake


async def refresh(c, client_id, token):
    return await c.post(
        "/token", data={"grant_type": "refresh_token", "refresh_token": token, "client_id": client_id}
    )


def client_row(app, client_id):
    [row] = [r for r in app.provider.list_clients() if r["client_id"] == client_id]
    return row


DAY = 24 * 3600


async def test_renewed_grant_slides_on_refresh_and_lapses_when_unused(remote, clock):
    service, app = remote
    async with web(app) as c:
        client_id = await register(c)
        code, verifier = await authorize(c, service, client_id, window="30d")
        issued = await tokens(c, client_id, code, verifier)
        row = client_row(app, client_id)
        assert row["renewed_while_used"] is True and row["last_used"] == clock.now
        assert row["active_until"]["refresh"] == clock.now + 30 * DAY

        clock.now += 20 * DAY  # Used again before the 30 days run out: the end moves.
        renewed = await refresh(c, client_id, issued["refresh_token"])
        assert renewed.status_code == 200, renewed.text
        row = client_row(app, client_id)
        assert row["active_until"]["refresh"] == clock.now + 30 * DAY and row["last_used"] == clock.now

        clock.now += 30 * DAY + 1  # Unused for 30 days: gone.
        lapsed = await refresh(c, client_id, renewed.json()["refresh_token"])
        assert lapsed.status_code == 400 and lapsed.json()["error"] == "invalid_grant"


async def test_24_hour_grant_keeps_its_end(remote, clock):
    service, app = remote
    async with web(app) as c:
        client_id = await register(c)
        code, verifier = await authorize(c, service, client_id, window="24h")
        issued = await tokens(c, client_id, code, verifier)
        end = clock.now + DAY
        row = client_row(app, client_id)
        assert row["renewed_while_used"] is False and row["never_expires"] is False

        clock.now += DAY / 2
        renewed = await refresh(c, client_id, issued["refresh_token"])
        assert renewed.status_code == 200, renewed.text
        assert renewed.json()["expires_in"] <= 900
        assert client_row(app, client_id)["active_until"]["refresh"] == end

        clock.now = end + 1
        assert (await refresh(c, client_id, renewed.json()["refresh_token"])).status_code == 400


async def test_never_grant_outlasts_long_idle_periods(remote, clock):
    service, app = remote
    async with web(app) as c:
        client_id = await register(c)
        code, verifier = await authorize(c, service, client_id, window="never")
        issued = await tokens(c, client_id, code, verifier)
        assert issued["expires_in"] <= 900  # Access tokens stay short; only the grant has no end.
        row = client_row(app, client_id)
        assert row["never_expires"] is True and row["renewed_while_used"] is False
        end = row["active_until"]["refresh"]
        assert end > clock.now + 99 * 365 * DAY

        clock.now += 400 * DAY  # Over a year without use.
        renewed = await refresh(c, client_id, issued["refresh_token"])
        assert renewed.status_code == 200, renewed.text
        assert client_row(app, client_id)["active_until"]["refresh"] == end


async def test_dashboard_revoke_ends_access_and_refresh_at_once(remote, tmp_path):
    from thumbly.interfaces.dashboard import CLIENT_HEADER, DASHBOARD_CLIENT, create_dashboard

    service, app = remote
    async with web(app) as c:
        client_id = await register(c)
        code, verifier = await authorize(c, service, client_id, window="30d")
        issued = await tokens(c, client_id, code, verifier)
        assert await app.provider.load_access_token(issued["access_token"]) is not None

        from thumbly.interfaces.agent_gateway import owner_of

        await service.coordinator.acquire(owner_of(client_id))  # The agent holds the phone.
        dashboard = create_dashboard(service)
        this_mac = httpx.ASGITransport(app=dashboard, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=this_mac, base_url="http://127.0.0.1:8770") as page:
            listed = (await page.get("/api/clients")).json()["clients"]
            assert [r["client_id"] for r in listed if not r["self"]] == [client_id]
            revoked = await page.post(
                f"/api/clients/{client_id}/revoke", json={}, headers={CLIENT_HEADER: DASHBOARD_CLIENT}
            )
            assert revoked.status_code == 200, revoked.text
        assert service.coordinator.lease is None  # Freed at once, not at the next sweep.

        assert await app.provider.load_access_token(issued["access_token"]) is None
        replay = await refresh(c, client_id, issued["refresh_token"])
        assert replay.status_code in {400, 401} and replay.json()["error"] in {
            "invalid_grant",
            "invalid_client",
        }


async def test_refresh_grace_period_is_bounded_and_cannot_chain(remote, monkeypatch):
    service, app = remote
    async with web(app) as c:
        client_id = await register(c)

        async def grant():
            code, verifier = await authorize(c, service, client_id, window="never")
            return await tokens(c, client_id, code, verifier)

        def refresh(token):
            return c.post(
                "/token",
                data={"grant_type": "refresh_token", "refresh_token": token, "client_id": client_id},
            )

        now = [time.time()]
        monkeypatch.setattr(
            agent_gateway, "time", SimpleNamespace(time=lambda: now[0], monotonic=time.monotonic)
        )

        # Reuse after the grace period revokes the family, even on a never-ending grant.
        first = await grant()
        assert (await refresh(first["refresh_token"])).status_code == 200
        now[0] += agent_gateway.REFRESH_GRACE_SECONDS + 1
        late = await refresh(first["refresh_token"])
        assert late.status_code == 400 and late.json()["error"] == "invalid_grant"

        # Chaining: the grace clock stays at the first rotation, and only one retry is allowed.
        a = await grant()
        b = (await refresh(a["refresh_token"])).json()  # Reply "lost".
        now[0] += 40
        c_pair = await refresh(a["refresh_token"])  # Grace retry at +40 s.
        assert c_pair.status_code == 200
        now[0] += 15  # +55 s: still inside the first window, but the retry was spent.
        assert (await refresh(a["refresh_token"])).status_code == 400
        assert await app.provider.load_access_token(c_pair.json()["access_token"]) is None  # Family ended.
        assert await app.provider.load_access_token(b["access_token"]) is None


async def test_unapproved_registrations_outlive_a_missed_request_and_the_oldest_is_evicted(remote):
    from thumbly.interfaces import agent_gateway

    service, app = remote
    provider = app.provider
    async with web(app) as c:
        first = await register(c)
        provider.prune(time.time() + agent_gateway.PENDING_SECONDS + 60)  # The owner missed the request.
        assert await provider.get_client(first) is not None  # The client can simply ask again.
        for _ in range(agent_gateway.PENDING_CLIENTS):
            provider.registrations.clear()  # Past the per-minute registration limit.
            await register(c)
        assert await provider.get_client(first) is None  # Oldest unapproved one made room.
        provider.prune(time.time() + agent_gateway.UNAPPROVED_CLIENT_SECONDS + 60)
        assert not [row for row in provider.list_clients() if not row["approved"]]


async def test_a_client_logout_is_not_undone_by_the_old_refresh_token(remote):
    service, app = remote
    async with web(app) as c:
        client_id = await register(c)
        code, verifier = await authorize(c, service, client_id, window="30d")
        first = await tokens(c, client_id, code, verifier)
        rotated = (await refresh(c, client_id, first["refresh_token"])).json()
        # What the /revoke endpoint does when a client logs out (RFC 7009).
        client = await app.provider.get_client(client_id)
        await app.provider.revoke_token(
            await app.provider.load_refresh_token(client, rotated["refresh_token"])
        )
        # Inside the lost-reply grace window, but the successor was revoked on purpose.
        retry = await refresh(c, client_id, first["refresh_token"])
        assert retry.status_code == 400 and retry.json()["error"] == "invalid_grant"


async def test_one_pending_request_per_client(remote):
    service, app = remote
    async with web(app) as c:
        client_id = await register(c)
        for _ in range(5):
            _, challenge = pkce()
            params = {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": CALLBACK,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "resource": PUBLIC + "/mcp",
            }
            assert (await c.get("/authorize", params=params)).status_code == 302
        assert [row["client_id"] for row in app.provider.list_pending()] == [client_id]
