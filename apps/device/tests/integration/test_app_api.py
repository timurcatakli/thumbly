"""Owner routes (/api/*) on the dashboard, and how the owner decides AI-client requests there."""

import asyncio
import html
import re
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from support import (
    CONSENT_PATH,
    LOCAL,
    PUBLIC,
    dashboard,
    local_token,
    make_config,
    owner_approve,
    register,
    running,
    start_authorization,
    tokens,
    web,
)

from thumbly.domain.errors import ThumblyError
from thumbly.interfaces.agent_gateway import (
    ADMIN_CALLS_PER_MINUTE,
    REFRESH_SECONDS,
    SCOPE_WORDS,
    STATUS_PATH,
    create_gateway_app,
)
from thumbly.interfaces.auth import digest
from thumbly.interfaces.dashboard import DASHBOARD_IDENTITY, create_dashboard
from thumbly.interfaces.mcp_server import ALL_SCOPES
from thumbly.service import Service

PUBLIC_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
LAPTOP = ("192.168.1.20", 50000)  # Another device on the network.


@pytest.fixture
async def thumbly(tmp_path):
    config = make_config(tmp_path)
    service = Service(config)
    await service.start()
    yield service, create_dashboard(service)
    await service.close()


def bearer(issued):
    return {"Authorization": f"Bearer {issued['access_token']}"}


async def public_request(pc, *, name="Claude", scope="phone:read phone:control"):
    """Register a browser client on the remote entry and park it on the consent page."""
    registered = await register(pc, callback=PUBLIC_CALLBACK, scope=scope, name=name)
    assert registered.status_code == 201, registered.text
    client_id = registered.json()["client_id"]
    started, request_id, verifier = await start_authorization(
        pc, PUBLIC, client_id, callback=PUBLIC_CALLBACK, scope=scope
    )
    assert started.status_code == 302 and request_id, started.text
    return client_id, request_id, verifier


async def status_of(pc, request_id):
    polled = await pc.get(STATUS_PATH, params={"request": request_id})
    assert polled.status_code == 200, polled.text
    return polled.json()


def refresh_window(provider, client_id):
    [row] = [r for r in provider.list_clients() if r["client_id"] == client_id]
    return row["active_until"]["refresh"] - time.time()


async def test_no_client_can_be_granted_owner_admin(thumbly):
    service, _ = thumbly
    local = create_gateway_app(service, LOCAL)
    async with running(local), web(local, LOCAL) as c:
        meta = (await c.get("/.well-known/oauth-authorization-server")).json()
        assert "owner:admin" not in meta.get("scopes_supported", [])
        refused = await register(c, scope="phone:read phone:control owner:admin")
        assert refused.status_code == 400 and refused.json()["error"] == "invalid_client_metadata"
        registered = await register(c)
        assert registered.status_code == 201, registered.text
        client_id = registered.json()["client_id"]
        assert registered.json()["scope"] == "phone:read phone:control"
        asked, request_id, _ = await start_authorization(c, LOCAL, client_id, scope="owner:admin")
        assert request_id is None and "error=invalid_scope" in asked.headers["location"]

        started, request_id, verifier = await start_authorization(c, LOCAL, client_id)
        assert started.status_code == 302 and request_id
        page = await c.get(started.headers["location"])
        assert page.status_code == 200 and "name='admin'" not in page.text
        code = parse_qs(urlsplit(owner_approve(service, request_id, window="24h")).query)["code"][0]
        issued = await tokens(c, LOCAL, client_id, code, verifier)
        assert issued.status_code == 200, issued.text
        assert issued.json()["scope"] == "phone:read phone:control"
        [row] = [r for r in local.provider.list_clients() if r["client_id"] == client_id]
        assert row["approved"] and row["scopes"] == ["phone:control", "phone:read"]
        assert abs(row["active_until"]["refresh"] - (time.time() + 24 * 3600)) < 60
        assert row["renewed_while_used"] is False and row["never_expires"] is False
        # The local entry has no owner routes, whatever the token.
        assert (await c.get("/api/status", headers=bearer(issued.json()))).status_code == 404

        # Cancel on the consent page returns the client an access_denied redirect.
        _, request_id, _ = await start_authorization(c, LOCAL, client_id)
        denied = await c.post(
            CONSENT_PATH, data={"request": request_id, "decision": "deny"}, headers={"Origin": LOCAL}
        )
        assert denied.status_code == 302 and "error=access_denied" in denied.headers["location"]


async def test_info_status_pause_operations_and_clients(thumbly):
    service, page = thumbly
    local = create_gateway_app(service, LOCAL)
    async with running(local), web(local, LOCAL) as lc, dashboard(page) as c:
        info = (await c.get("/api/info")).json()
        assert info["name"] == "Thumbly" and "entry" not in info
        assert info["mcp_url"] == "http://127.0.0.1:8771/mcp" and info["public_url"] == PUBLIC + "/mcp"
        assert info["scopes"] == ALL_SCOPES

        status = await c.get("/api/status")
        assert status.status_code == 200, status.text
        assert "setup_mode" not in status.json() and "mcp" in status.json()
        assert status.json()["pending_consents"] == 0
        assert (await c.get("/api/capabilities")).status_code == 200

        paused = await c.post("/api/pause", json={})
        assert paused.status_code == 200, paused.text
        assert paused.json()["state"] == "SUCCEEDED" and paused.json()["kind"] == "pause"
        pause_id = paused.json()["id"]

        listed = await c.get("/api/operations")
        assert listed.status_code == 200, listed.text
        [pause] = [op for op in listed.json()["operations"] if op["id"] == pause_id]
        assert pause["client"] == DASHBOARD_IDENTITY and "owner" not in pause
        page = await c.get("/api/operations", params={"limit": 1})
        assert len(page.json()["operations"]) == 1
        single = await c.get(f"/api/operations/{pause_id}")
        assert single.status_code == 200 and single.json()["state"] == "SUCCEEDED"
        assert (await c.get("/api/operations/nope")).status_code == 404

        client_id, _ = await local_token(lc, service, name="Agent")
        other_id, other = await local_token(lc, service, name="Other agent")
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        assert (await lc.post("/mcp", json=ping, headers=bearer(other))).status_code == 200
        clients = (await c.get("/api/clients")).json()["clients"]
        assert {row["client_id"]: row["self"] for row in clients} == {client_id: False, other_id: False}
        revoked = await c.post(f"/api/clients/{other_id}/revoke", json={})
        assert revoked.status_code == 200 and revoked.json() == {"revoked": other_id}
        clients = (await c.get("/api/clients")).json()["clients"]
        assert [row["client_id"] for row in clients] == [client_id]
        assert (await lc.post("/mcp", json=ping, headers=bearer(other))).status_code == 401
        assert (await c.post(f"/api/clients/{other_id}/revoke", json={})).status_code == 404


async def test_setup_and_discover_routes(thumbly):
    service, page = thumbly
    async with dashboard(page) as c:
        discovered = await c.get("/api/discover")
        assert discovered.status_code == 200 and discovered.json()["usb"][0]["udid"] == "simulated-phone"

        queued = await c.post(
            "/api/setup/pair",
            json={"payload": {"udid": "simulated-phone", "owner_confirmed": True}},
        )
        assert queued.status_code == 202, queued.text
        assert queued.json()["state"] == "QUEUED" and queued.json()["kind"] == "pair"
        operation_id = queued.json()["id"]
        for _ in range(100):
            polled = await c.get(f"/api/operations/{operation_id}")
            assert polled.status_code == 200
            if polled.json()["state"] not in {"QUEUED", "DISPATCHED"}:
                break
            await asyncio.sleep(0.05)
        assert polled.json()["state"] == "SUCCEEDED", polled.text
        assert polled.json()["result"]["trusted"] is True
        assert service.coordinator.lease is not None  # Setup auto-acquired the dashboard's lease.
        # The lease lapsed between page steps: setup takes control again instead of failing.
        await service.coordinator.drop_control()
        mounted = await c.post("/api/setup/mount_ddi", json={})
        assert mounted.status_code in {200, 202}, mounted.text
        assert service.coordinator.lease is not None

        unconfirmed = await c.post("/api/setup/pair", json={"payload": {"udid": "simulated-phone"}})
        assert unconfirmed.status_code == 400, unconfirmed.text
        assert unconfirmed.json()["error"]["code"] == "OWNER_CONFIRMATION_REQUIRED"
        unknown = await c.post("/api/setup/nope", json={})
        assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "UNKNOWN_SETUP_ACTION"
        bad_id = await c.post("/api/setup/mount_ddi", json={"operation_id": "x" * 65})
        assert bad_id.status_code == 400 and bad_id.json()["error"]["code"] == "INVALID_OPERATION_ID"


async def test_reset_needs_the_name_typed(thumbly):
    service, page = thumbly
    async with dashboard(page) as c:
        wrong = await c.post("/api/reset", json={"name": "Thumb"})
        assert wrong.status_code == 400 and wrong.json()["error"]["code"] == "RESET_NAME_MISMATCH"
        right = await c.post("/api/reset", json={"name": " thumbly "})
        assert right.status_code == 200 and right.json()["state"] == "reset"
        assert service.bindings.read() is None
        assert (await c.get("/api/overview")).json()["bound"] is False


async def test_dashboard_keeps_control_and_its_operations_across_the_sweep(thumbly):
    service, page = thumbly
    gateway = service.gateway
    async with dashboard(page) as c:
        paused = (await c.post("/api/pause", json={})).json()
        bridge = gateway.bridges[DASHBOARD_IDENTITY][0]
        await bridge.ensure_control()
        lease = service.coordinator.lease
        assert lease is not None
        # The sweep runs every 30 s and on every revoke; the page has no OAuth client behind it.
        await gateway.sweep()
        assert gateway.bridges[DASHBOARD_IDENTITY][0] is bridge and service.coordinator.lease is lease
        single = await c.get(f"/api/operations/{paused['id']}")
        assert single.status_code == 200 and single.json()["kind"] == "pause"


async def test_diagnostics_are_redacted(thumbly):
    service, page = thumbly
    async with dashboard(page) as c:
        report = await c.get("/api/diagnostics")
        assert report.status_code == 200, report.text
        data = report.json()
        assert {"versions", "config", "status", "journal", "logs", "network"} <= set(data)
        assert data["config"]["public_url"] == PUBLIC and data["config"]["backend"] == "simulated"
        assert "samples" not in data["status"].get("profile", {}).get("calibration", {})
        assert isinstance(data["logs"], list) and isinstance(data["journal"]["recent"], list)
        assert "interface" in data["network"]


async def test_owner_routes_need_the_dashboard(thumbly):
    _, page = thumbly
    # Another device on the network gets nothing, whatever it sends.
    async with dashboard(page, client=LAPTOP) as laptop:
        for path in ("/api/overview", "/api/status", "/api/operations", "/api/clients", "/api/consents"):
            refused = await laptop.get(path)
            assert refused.status_code == 403, path
            assert refused.json()["error"]["code"] == "THIS_MAC_ONLY"
        assert (await laptop.post("/api/pause", json={})).status_code == 403
    # A page that rebinds its own name to 127.0.0.1 still sends its own Host header.
    async with dashboard(page) as c:
        rebound = await c.get("/api/status", headers={"host": "attacker.example:8770"})
        assert rebound.status_code == 403 and rebound.json()["error"]["code"] == "HOST_NOT_ALLOWED"
        forged = await c.post("/api/pause", json={}, headers={"x-thumbly-client": "other"})
        assert forged.status_code == 403 and forged.json()["error"]["code"] == "CLIENT_HEADER_REQUIRED"
    # The dashboard polls status while the owner has it open: its higher limit applies.
    assert ADMIN_CALLS_PER_MINUTE == 300
    async with dashboard(page) as c:
        codes = {(await c.get("/api/status")).status_code for _ in range(130)}
        assert codes == {200}


async def test_owner_decides_pending_consents_of_the_remote_entry(thumbly):
    service, page = thumbly
    public = create_gateway_app(service)
    local = create_gateway_app(service, LOCAL)
    async with running(public), running(local), web(local, LOCAL) as lc, web(public, PUBLIC) as pc:
        async with dashboard(page) as c:
            assert (await c.get("/api/consents")).json() == {"consents": []}

            client_id, request_id, verifier = await public_request(pc)
            listed = await c.get("/api/consents")
            assert listed.status_code == 200, listed.text
            [pending] = listed.json()["consents"]
            assert pending["handle"] == digest(request_id) and request_id not in listed.text
            assert pending["client_name"] == "Claude" and pending["client_id"] == client_id
            assert pending["scopes"] == ["phone:read", "phone:control"] and pending["issuer"] == PUBLIC
            assert 0 < pending["expires_in"] <= 600 and pending["redirect_hosts"] == ["claude.ai"]
            assert (await c.get("/api/status")).json()["pending_consents"] == 1
            assert await status_of(pc, request_id) == {"state": "pending"}

            # Owner allows for 30 days from the dashboard; the browser's poll picks up the redirect once.
            choice = {"decision": "allow", "window": "30d"}
            decided = await c.post(f"/api/consents/{pending['handle']}", json=choice)
            assert decided.status_code == 200, decided.text
            assert decided.json() == {
                "decided": "allow",
                "scopes": ["phone:read", "phone:control"],
                "window": REFRESH_SECONDS,
            }
            assert (await c.get("/api/status")).json()["pending_consents"] == 0
            assert (await c.get("/api/consents")).json() == {"consents": []}
            polled = await status_of(pc, request_id)
            assert polled["state"] == "decided" and polled["redirect"].startswith(PUBLIC_CALLBACK + "?")
            query = parse_qs(urlsplit(polled["redirect"]).query)
            assert query["state"] == ["abc"] and query["code"]
            assert await status_of(pc, request_id) == {"state": "gone"}  # Handed over exactly once.

            # The code belongs to the remote issuer: the local entry refuses it.
            code = query["code"][0]
            foreign = await tokens(lc, LOCAL, client_id, code, verifier, callback=PUBLIC_CALLBACK)
            assert foreign.status_code in {400, 401}, foreign.text
            exchanged = await tokens(pc, PUBLIC, client_id, code, verifier, callback=PUBLIC_CALLBACK)
            assert exchanged.status_code == 200, exchanged.text
            assert exchanged.json()["scope"] == "phone:read phone:control"
            [row] = [r for r in public.provider.list_clients() if r["client_id"] == client_id]
            assert row["approved"] and row["issuer"] == PUBLIC
            assert row["scopes"] == ["phone:control", "phone:read"]
            assert abs(refresh_window(public.provider, client_id) - REFRESH_SECONDS) < 60
            ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
            assert (await pc.post("/mcp", json=ping, headers=bearer(exchanged.json()))).status_code == 200

            # Deny from the dashboard: the client gets access_denied, nothing is granted.
            other_id, request_id, _ = await public_request(pc, name="Other agent")
            handle = digest(request_id)
            denied = await c.post(f"/api/consents/{handle}", json={"decision": "deny"})
            assert denied.status_code == 200 and denied.json() == {"decided": "deny", "scopes": []}
            polled = await status_of(pc, request_id)
            assert polled["state"] == "decided" and "error=access_denied" in polled["redirect"]
            approved = [r["client_id"] for r in public.provider.list_clients() if r["approved"]]
            assert other_id not in approved
            # A decided handle is unknown afterwards, as is one that never existed.
            for bad in (handle, "nope", digest("nope")):
                gone = await c.post(f"/api/consents/{bad}", json=choice)
                assert gone.status_code == 404 and gone.json()["error"]["code"] == "CONSENT_EXPIRED", bad

            # An unknown duration is refused, and the request stays pending.
            _, request_id, _ = await public_request(pc, name="Waiting")
            refused = await c.post(
                f"/api/consents/{digest(request_id)}", json={"decision": "allow", "window": "8h"}
            )
            assert refused.status_code == 422 and refused.json()["error"]["code"] == "INVALID_WINDOW"
            assert await status_of(pc, request_id) == {"state": "pending"}


async def test_consent_page_links_to_the_dashboard(thumbly):
    service, _ = thumbly
    local = create_gateway_app(service, LOCAL)
    async with running(local), web(local, LOCAL) as c:
        registered = await register(c)
        client_id = registered.json()["client_id"]
        started, request_id, _ = await start_authorization(c, LOCAL, client_id)
        page = await c.get(started.headers["location"])
        assert page.status_code == 200, page.text
        text = html.unescape(page.text)
        assert f"thumbly://access?r={digest(request_id)}" in text
        assert "<svg" not in text and "value='deny'" in text and "Wi-Fi" not in text
        # The dashboard is the only way to approve: no PIN, no credential, no duration on this page.
        assert "name='pin'" not in text and "name='credential'" not in text and "name='window'" not in text
        assert all(words in text for words in SCOPE_WORDS.values())
        assert STATUS_PATH in text

        csp = page.headers["content-security-policy"]
        assert "script-src 'nonce-" in csp and "connect-src 'self'" in csp and "default-src 'none'" in csp
        nonce = re.search(r"script-src 'nonce-([^']+)'", csp)
        assert nonce and f"<script nonce='{nonce.group(1)}'>" in page.text
        assert "x-thumbly-nonce" not in page.headers and "nonce" not in page.headers.get(
            "x-thumbly-nonce", ""
        )
        assert page.headers["cache-control"] == "no-store"
        # Every render draws a fresh nonce; pages without a script carry no script-src at all.
        second = await c.get(started.headers["location"])
        assert second.headers["content-security-policy"] != csp
        expired = await c.get(CONSENT_PATH, params={"request": "nope"})
        assert expired.status_code == 400 and "script-src" not in expired.headers["content-security-policy"]

        assert await status_of(c, request_id) == {"state": "pending"}
        assert await status_of(c, "nope") == {"state": "gone"}
        assert (await c.get(STATUS_PATH)).json() == {"state": "gone"}


async def test_the_owner_resumes_and_clears_activity(thumbly):
    service, page = thumbly
    async with dashboard(page) as c:
        assert (await c.post("/api/pause", json={})).json()["result"]["paused"] is True
        with pytest.raises(ThumblyError) as paused:
            await service.phone.screenshot()  # The brake hides the screen too.
        assert paused.value.code == "PAUSED"
        resumed = await c.post("/api/resume", json={})
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["result"] == {"paused": False}
        status = (await c.get("/api/status")).json()
        assert status["control"]["paused"] is False and status["control"]["active"] is False  # Handed back.

        listed = (await c.get("/api/operations", params={"actions": "1"})).json()
        assert [op["kind"] for op in listed["operations"]] == ["resume", "pause"] and listed["total"] == 2
        assert (await c.get("/api/operations")).json()["total"] > 2  # acquire and release too.

        cleared = await c.post("/api/operations/clear", json={})
        assert cleared.status_code == 200 and cleared.json()["cleared"] >= 4
        assert (await c.get("/api/operations")).json()["total"] == 0
