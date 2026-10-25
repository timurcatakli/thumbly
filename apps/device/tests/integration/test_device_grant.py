"""Device authorization grant (RFC 8628): scripts approved in the Thumbly app like MCP clients."""

import time
from types import SimpleNamespace

import pytest
from support import LOCAL, PUBLIC, make_config, raises_code, running, web

from thumbly.interfaces import agent_gateway, device_grant
from thumbly.interfaces.agent_gateway import create_gateway_app, grant_from_choice
from thumbly.interfaces.auth import digest
from thumbly.interfaces.device_grant import DEVICE_GRANT, DEVICE_PENDING, POLLS
from thumbly.service import Service

ACCEPT = {"Accept": "application/json, text/event-stream"}


@pytest.fixture
async def entries(tmp_path):
    config = make_config(tmp_path)
    service = Service(config)
    await service.start()
    public = create_gateway_app(service)
    local = create_gateway_app(service, LOCAL)
    async with running(public), running(local):
        yield service, public, local
    await service.close()


def grant_of(app):
    return app.app.grant  # EntrySecurity -> DeviceGrantRoutes


async def start(c, name="nightly.py", **extra):
    r = await c.post("/oauth/device_authorization", data={"client_name": name, **extra})
    assert r.status_code == 200, r.text
    return r.json()


async def poll(c, started):
    return await c.post(
        "/token",
        data={
            "grant_type": DEVICE_GRANT,
            "device_code": started["device_code"],
            "client_id": started["client_id"],
        },
    )


def approve(service, handle, window="never", allow=True):
    """What the dashboard does when the owner clicks Approve (app_api decide_consent)."""
    provider = service.auth_provider
    _, value = provider.pending_by_handle(handle)
    if not allow:
        return provider.decide_handle(handle, None, 0)
    granted, seconds = grant_from_choice(value["scopes"], window)
    return provider.decide_handle(handle, granted, seconds)


def pending_row(service, started):
    rows = [r for r in service.auth_provider.list_pending() if r["client_id"] == started["client_id"]]
    assert len(rows) == 1, rows
    return rows[0]


async def call(c, access, name="thumbly_status", arguments=None):
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }
    return await c.post("/mcp", json=body, headers={"Authorization": f"Bearer {access}", **ACCEPT})


async def test_metadata_advertises_device_grant(entries):
    _, public, local = entries
    for app, origin in ((public, PUBLIC), (local, LOCAL)):
        async with web(app, origin) as c:
            r = await c.get("/.well-known/oauth-authorization-server")
            assert r.status_code == 200, r.text
            metadata = r.json()
            assert metadata["device_authorization_endpoint"] == origin + "/oauth/device_authorization"
            assert DEVICE_GRANT in metadata["grant_types_supported"]
            assert "authorization_code" in metadata["grant_types_supported"]
            assert int(r.headers["content-length"]) == len(r.content)


async def test_device_flow_approved_on_dashboard(entries):
    service, public, local = entries
    async with web(local, LOCAL) as c, web(public, PUBLIC) as pc:
        started = await start(c)
        assert started["interval"] == 5 and started["expires_in"] == 600
        assert len(started["user_code"]) == 9 and started["user_code"][4] == "-"
        row = pending_row(service, started)
        # Same list the dashboard shows; never the admin scope, even on the local entry.
        assert row["kind"] == "device" and row["user_code"] == started["user_code"]
        assert row["scopes"] == ["phone:read", "phone:control"] and row["client_name"] == "nightly.py"
        assert started["verification_uri"] == f"thumbly://access?r={row['handle']}"

        first = await poll(c, started)
        assert first.status_code == 400 and first.json()["error"] == "authorization_pending"
        hurried = await poll(c, started)
        assert hurried.json()["error"] == "slow_down"
        assert grant_of(local).polls[row["handle"]][1] == 10

        assert approve(service, row["handle"]) == ""
        assert service.auth_provider.list_pending() == []
        issued = await poll(c, started)
        assert issued.status_code == 200, issued.text
        tokens = issued.json()
        assert tokens["scope"] == "phone:read phone:control" and tokens["expires_in"] == 900
        # Single use: the device code is spent.
        again = await poll(c, started)
        assert again.json()["error"] in {"invalid_grant", "expired_token"}

        r = await call(c, tokens["access_token"])
        assert r.status_code == 200, r.text
        assert r.json()["result"]["isError"] is False
        # Issuer binding: a LOCAL grant does not open the remote entry.
        assert (await call(pc, tokens["access_token"])).status_code == 401

        # Standard refresh grant; the client shows as approved and never-ending.
        refreshed = await c.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": started["client_id"],
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        (client,) = [r for r in local.provider.list_clients() if r["client_id"] == started["client_id"]]
        assert client["approved"] and client["never_expires"]

        # Owner removes it: the next call is refused.
        assert service.auth_provider.revoke_client(started["client_id"])
        assert (await call(c, refreshed.json()["access_token"])).status_code == 401


async def test_denied_expired_and_replaced_requests(entries, monkeypatch):
    service, _, local = entries
    async with web(local, LOCAL) as c:
        denied = await start(c, "denied.py")
        approve(service, pending_row(service, denied)["handle"], allow=False)
        r = await poll(c, denied)
        assert r.json()["error"] == "access_denied"

        # Re-requesting with the cached client_id reuses the client and replaces its old request.
        first = await start(c, "again.py")
        second = await start(c, "again.py", client_id=first["client_id"])
        assert second["client_id"] == first["client_id"]
        assert (await poll(c, first)).json()["error"] in {"expired_token", "invalid_grant"}
        assert (await poll(c, second)).json()["error"] == "authorization_pending"
        # A client_id that is not a device client of this issuer gets a fresh client.
        other = await start(c, "other.py", client_id="not-a-client")
        assert other["client_id"] != "not-a-client"

        # Lapsed: the row is pruned and a late poll learns expired_token.
        later = time.time() + 601
        monkeypatch.setattr(
            agent_gateway, "time", SimpleNamespace(time=lambda: later, monotonic=time.monotonic)
        )
        monkeypatch.setattr(device_grant, "time", SimpleNamespace(time=lambda: later))
        state = grant_of(local).polls[digest(second["device_code"])]
        state[0] = later - 60
        assert (await poll(c, second)).json()["error"] == "expired_token"
        assert [
            r for r in service.auth_provider.list_pending() if r["client_id"] == second["client_id"]
        ] == []


async def test_device_request_bounds(entries):
    service, _, local = entries
    grant = grant_of(local)
    async with web(local, LOCAL) as c:
        refused = await c.post(
            "/oauth/device_authorization", data={"client_name": "x", "scope": "phone:read owner:admin"}
        )
        assert refused.status_code == 400 and refused.json()["error"] == "invalid_scope"
        assert (await c.post("/oauth/device_authorization", data={"client_name": ""})).status_code == 400
        assert (
            await c.post("/oauth/device_authorization", data={"client_name": "x" * 65})
        ).status_code == 400
        big = await c.post("/oauth/device_authorization", data={"client_name": "x" * 5000})
        assert big.status_code == 413

        started = [await start(c, f"s{i}.py") for i in range(DEVICE_PENDING)]
        full = await c.post("/oauth/device_authorization", data={"client_name": "sixth.py"})
        assert full.status_code == 429 and full.json()["error"] == "slow_down"

        # A mismatched client_id never collects someone else's decision.
        victim = started[0]
        approve(service, pending_row(service, victim)["handle"])
        stolen = await c.post(
            "/token",
            data={
                "grant_type": DEVICE_GRANT,
                "device_code": victim["device_code"],
                "client_id": started[1]["client_id"],
            },
        )
        assert stolen.json()["error"] == "invalid_grant"
        assert (await poll(c, victim)).status_code == 200

        # Poll cap: the request is deleted past POLLS.
        capped = started[1]
        handle = pending_row(service, capped)["handle"]
        grant.polls[handle][2] = POLLS
        assert (await poll(c, capped)).json()["error"] == "expired_token"
        assert all(r["client_id"] != capped["client_id"] for r in service.auth_provider.list_pending())

        # Garbage device codes spend a service-wide budget; once it is gone, junk is refused, but real
        # scripts still get their answer: pending ones keep polling, approved ones get tokens.
        junk = {"grant_type": DEVICE_GRANT, "device_code": "x", "client_id": "y"}
        grant.failed = []  # Earlier mismatches above spent some of it.
        for _ in range(device_grant.FAILED_POLLS_PER_MINUTE):
            assert (await c.post("/token", data=junk)).json()["error"] == "invalid_grant"
        flooded = await c.post("/token", data=junk)
        assert flooded.status_code == 429 and flooded.json()["error"] == "slow_down"
        assert (await poll(c, started[2])).json()["error"] == "authorization_pending"
        approve(service, pending_row(service, started[3])["handle"])
        assert (await poll(c, started[3])).status_code == 200

        # More than 16 form fields is a bad request on both endpoints, not a server error.
        many = {f"f{i}": "x" for i in range(17)}
        for path in ("/token", "/oauth/device_authorization"):
            r = await c.post(path, data={"grant_type": DEVICE_GRANT, **many})
            assert r.status_code == 400 and r.json()["error"] == "invalid_request", (path, r.text)


async def test_sliding_window_lapses_and_never_does_not(entries, monkeypatch):
    service, _, local = entries
    async with web(local, LOCAL) as c:
        sliding = await start(c, "monthly.py")
        approve(service, pending_row(service, sliding)["handle"], window="30d")
        sliding_tokens = (await poll(c, sliding)).json()
        forever = await start(c, "forever.py")
        approve(service, pending_row(service, forever)["handle"], window="never")
        forever_tokens = (await poll(c, forever)).json()

        def refresh(started, tokens):
            return c.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                    "client_id": started["client_id"],
                },
            )

        later = time.time() + 31 * 24 * 3600
        monkeypatch.setattr(
            agent_gateway, "time", SimpleNamespace(time=lambda: later, monotonic=time.monotonic)
        )
        assert (await refresh(sliding, sliding_tokens)).status_code == 400
        assert (await refresh(forever, forever_tokens)).status_code == 200

    # Unknown windows are refused like in the Thumbly app, and the request stays pending.
    async with web(local, LOCAL) as c:
        monkeypatch.undo()
        odd = await start(c, "odd.py")
        handle = pending_row(service, odd)["handle"]
        with raises_code("INVALID_WINDOW"):
            approve(service, handle, window="1h")
        assert pending_row(service, odd)["handle"] == handle


async def test_other_grants_pass_through(entries):
    _, public, _ = entries
    async with web(public, PUBLIC) as c:
        r = await c.post(
            "/token", data={"grant_type": "authorization_code", "code": "nope", "client_id": "nope"}
        )
        assert r.status_code == 401 and r.json()["error"] == "invalid_client"
        r = await c.post("/token", data={"grant_type": "password"})
        assert r.status_code in {400, 401}
