"""SDK behaviour against a fake Thumbly server (httpx.MockTransport)."""

import json
import os
import stat
import threading
import time
from urllib.parse import parse_qs

import httpx
import pytest

from thumbly_sdk import (
    ApprovalDenied,
    ApprovalRequired,
    FrameExpired,
    OutcomeUnknown,
    Phone,
    RateLimited,
    ResponseTooLarge,
    ThumblyError,
)
from thumbly_sdk._auth import Store
from thumbly_sdk.phone import DEFAULT_URL, base_url

URL = "https://thumbly.example"
PNG = "iVBORw0KGgo="  # Any base64 works; the SDK does not decode images.


class FakeServer:
    """Just enough OAuth and MCP to drive the SDK; records every tool call."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.device_requests = 0
        self.refreshes = 0
        self.polls: list[str] = ["authorization_pending"]
        self.tool_results: list = []  # Callables or dicts consumed per tools/call, then default.
        self.drop_next = 0
        self.frame = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(
                200,
                json={
                    "issuer": URL,
                    "token_endpoint": URL + "/token",
                    "device_authorization_endpoint": URL + "/oauth/device_authorization",
                },
            )
        if path == "/oauth/device_authorization":
            self.device_requests += 1
            return httpx.Response(
                200,
                json={
                    "device_code": "dc",
                    "user_code": "BCDF-GHJK",
                    "verification_uri": URL,
                    "expires_in": 600,
                    "interval": 0.1,
                    "client_id": "cid",
                },
            )
        if path == "/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if form["grant_type"] == "refresh_token":
                self.refreshes += 1
                if form["refresh_token"] == "dead":
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json=self.tokens(f"r{self.refreshes}"))
            outcome = self.polls.pop(0) if self.polls else "ok"
            if outcome != "ok":
                return httpx.Response(400, json={"error": outcome})
            return httpx.Response(200, json=self.tokens("r0"))
        if path == "/mcp":
            if self.drop_next:
                self.drop_next -= 1
                raise httpx.ConnectError("dropped")
            message = json.loads(request.content)
            if message["method"] == "tools/list":
                tools = [{"name": "thumbly_launch_app", "inputSchema": {"properties": {"name": {}}}}]
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": message["id"], "result": {"tools": tools}}
                )
            name, arguments = message["params"]["name"], message["params"]["arguments"]
            self.calls.append((name, arguments))
            result = self.tool_results.pop(0) if self.tool_results else None
            if callable(result):
                result = result(name, arguments)
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": message["id"], "result": result or self.ok(arguments)}
            )
        return httpx.Response(404)

    @staticmethod
    def tokens(refresh: str) -> dict:
        return {
            "access_token": "a-" + refresh,
            "expires_in": 900,
            "refresh_token": refresh,
            "scope": "phone:read",
        }

    def observation(self) -> dict:
        self.frame += 1
        return {
            "metadata": {"frame_id": f"f{self.frame}", "geometry_epoch": "g", "width": 100, "height": 200}
        }

    def ok(self, arguments: dict) -> dict:
        data = {"operation": {"id": arguments.get("operation_id"), "state": "SUCCEEDED"}}
        content: list = [{"type": "text", "text": "{}"}]
        if arguments.get("observe", True):
            data["observation"] = self.observation()
            content.append({"type": "image", "data": PNG, "mimeType": "image/jpeg"})
        return {"content": content, "structuredContent": data, "isError": False}


@pytest.fixture
def server():
    return FakeServer()


def phone_for(server, tmp_path, **options):
    options.setdefault("allow_approval", True)
    return Phone(
        URL,
        cache_dir=tmp_path,
        transport=httpx.MockTransport(server.handler),
        notify=lambda text: None,
        **options,
    )


def screenshot_result(server):
    observation = server.observation()
    return {
        "content": [{"type": "image", "data": PNG, "mimeType": "image/jpeg"}],
        "structuredContent": observation,
        "isError": False,
    }


def test_addresses():
    assert DEFAULT_URL == "http://127.0.0.1:8771"
    assert base_url() == base_url(None) == "http://127.0.0.1:8771"
    assert base_url("http://127.0.0.1:9000/") == "http://127.0.0.1:9000"
    assert base_url("http://LOCALHOST:8771") == "http://localhost:8771"
    assert base_url("http://[::1]:8771") == "http://[::1]:8771"
    assert base_url("https://mac.tailnet.ts.net/") == "https://mac.tailnet.ts.net"
    for bad in (
        "http://192.168.1.4:8771",
        "http://mac.tailnet.ts.net",
        "thumbly-ab12",
        "127.0.0.1:8771",
        "ftp://127.0.0.1",
        "https://u:p@mac.ts.net",
        "https://mac.ts.net/x",
    ):
        with pytest.raises(ValueError):
            base_url(bad)


def test_default_phone_targets_loopback(tmp_path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with Phone(cache_dir=tmp_path, transport=transport, allow_approval=True, notify=print) as phone:
        assert phone.url == "http://127.0.0.1:8771"
        with pytest.raises(ThumblyError, match="DISCOVERY_FAILED"):
            phone.connect()
    assert seen and seen[0].startswith("http://127.0.0.1:8771/")


def test_cli_url_is_optional(monkeypatch):
    from thumbly_sdk import cli

    made = []

    class Stub:
        def __init__(self, url, **options):
            made.append(url)
            self.mcp = self

        def tools(self):
            return {}

        def call(self, tool, values):
            made.append((tool, values))
            return type("R", (), {"data": {}, "is_error": False})()

        def close(self):
            pass

    monkeypatch.setattr(cli, "Phone", Stub)
    assert cli.main(["call", "thumbly_button", "button=home"]) == 0
    assert made == [None, ("thumbly_button", {"button": "home"})]
    made.clear()
    assert cli.main(["call", "https://mac.ts.net", "thumbly_status"]) == 0
    assert made == ["https://mac.ts.net", ("thumbly_status", {})]


def test_first_run_approves_caches_privately_and_second_run_does_not_ask(server, tmp_path):
    with phone_for(server, tmp_path, name="nightly.py") as phone:
        assert not phone.approved
        phone.connect()
        assert phone.approved
        phone.open_app("com.apple.mobilenotes").type("hello").tap(0.5, 0.5).swipe(0.5, 0.8, 0.5, 0.2).home()
    assert server.device_requests == 1
    names = [name for name, _ in server.calls]
    assert names[:5] == [
        "thumbly_launch_app",
        "thumbly_type_text",
        "thumbly_tap",
        "thumbly_swipe",
        "thumbly_button",
    ]
    assert names[-1] == "thumbly_control"  # close() releases control.
    launch, typed, tap, swipe = (args for _, args in server.calls[:4])
    assert launch["bundle_id"] == "com.apple.mobilenotes"
    assert typed["text"] == "hello"
    # Each touch refers to the frame the previous action returned.
    assert tap["frame_id"] == "f2" and swipe["frame_id"] == "f3"
    assert len({args["operation_id"] for _, args in server.calls}) == len(server.calls)

    (cache,) = tmp_path.glob("*.json")
    assert stat.S_IMODE(cache.stat().st_mode) == 0o600
    assert json.loads(cache.read_text())["client_id"] == "cid"

    with phone_for(server, tmp_path, allow_approval=False) as again:
        again.home()
    assert server.device_requests == 1 and server.refreshes == 0


def test_expired_access_refreshes_headlessly_and_dead_grant_needs_approval(server, tmp_path):
    with phone_for(server, tmp_path) as phone:
        phone.status()
    store = Store(URL, tmp_path)
    data = store.load()
    store.save({**data, "access_expires": 0})
    with phone_for(server, tmp_path, allow_approval=False) as phone:
        phone.status()
    assert server.refreshes == 1 and server.device_requests == 1

    store.save({**store.load(), "access_expires": 0, "refresh_token": "dead"})
    started = time.monotonic()
    with pytest.raises(ApprovalRequired):
        phone_for(server, tmp_path, allow_approval=False).status()
    assert time.monotonic() - started < 1


def test_denied_approval(server, tmp_path):
    server.polls = ["authorization_pending", "slow_down", "access_denied"]
    phone = phone_for(server, tmp_path)
    phone.auth.sleep = lambda seconds: None
    with pytest.raises(ApprovalDenied):
        phone.status()
    assert not Store(URL, tmp_path).load().get("access_token")


def test_concurrent_refresh_rotates_once(server, tmp_path):
    with phone_for(server, tmp_path) as phone:
        phone.status()
    store = Store(URL, tmp_path)
    store.save({**store.load(), "access_expires": 0})
    phones = [phone_for(server, tmp_path, allow_approval=False) for _ in range(4)]
    gate = threading.Barrier(len(phones))

    def run(p):
        gate.wait()
        p.auth.token()

    threads = [threading.Thread(target=run, args=(p,)) for p in phones]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # One process rotated; the others found the fresh pair under the lock.
    assert server.refreshes == 1


def test_dropped_connection_retries_same_operation_then_reads_journal(server, tmp_path):
    phone = phone_for(server, tmp_path, observe=False)
    phone.status()
    server.drop_next = 1
    phone.home()
    # The dropped attempt never reached the fake server; the retry did, exactly once.
    assert [name for name, _ in server.calls].count("thumbly_button") == 1

    server.drop_next = 2
    server.tool_results = [
        {"content": [], "structuredContent": {"id": "x", "state": "SUCCEEDED"}, "isError": False}
    ]
    with pytest.raises(OutcomeUnknown) as info:
        phone.key("enter")
    assert info.value.detail["operation"]["state"] == "SUCCEEDED"
    looked_up = server.calls[-1]
    assert looked_up[0] == "thumbly_operation_status"


def test_frame_expired_is_raised_not_retried(server, tmp_path):
    phone = phone_for(server, tmp_path)
    server.tool_results = [screenshot_result(server)]
    frame = phone.screenshot()
    assert frame.id == "f1" and frame.image
    refused = {"code": "FRAME_EXPIRED", "dispatched": False}
    server.tool_results = [{"content": [], "structuredContent": {"error": refused}, "isError": True}]
    with pytest.raises(FrameExpired) as info:
        frame.tap(10, 20)
    assert info.value.dispatched is False
    taps = [args for name, args in server.calls if name == "thumbly_tap"]
    assert len(taps) == 1
    assert taps[0]["x"] == pytest.approx(10.5 / 100) and taps[0]["y"] == pytest.approx(20.5 / 200)
    with pytest.raises(ValueError):
        frame.tap(1.5, 0.5)
    with pytest.raises(ValueError):
        frame.tap(100, 5)


def test_rate_limit_backoff_then_error(server, tmp_path):
    phone = phone_for(server, tmp_path)
    phone.mcp.sleep = lambda seconds: None
    limited = {"content": [], "structuredContent": {"error": {"code": "RATE_LIMITED"}}, "isError": True}
    server.tool_results = [
        limited,
        limited,
        {"content": [], "structuredContent": {"ok": 1}, "isError": False},
    ]
    assert phone.status() == {"ok": 1}
    server.tool_results = [limited] * 4
    with pytest.raises(RateLimited):
        phone.status()


def test_type_routes_long_or_unicode_text_through_clipboard(server, tmp_path):
    phone = phone_for(server, tmp_path, observe=False)
    phone.type("hi there").type("héllo").type("x" * 201)
    assert [name for name, _ in server.calls] == [
        "thumbly_type_text",
        "thumbly_send_text",
        "thumbly_send_text",
    ]
    with pytest.raises(ValueError):
        phone.paste("x" * 70_000)
    with pytest.raises(ValueError):
        phone.open_app("Notes")  # Bundle ids only; apps("notes") finds it.


def test_response_cap(server, tmp_path, monkeypatch):
    import thumbly_sdk._mcp as mcp

    phone = phone_for(server, tmp_path)
    phone.status()
    monkeypatch.setattr(mcp, "MAX_RESPONSE", 10)
    with pytest.raises(ResponseTooLarge):
        phone.status()


def test_cache_ignores_other_addresses_and_garbage(tmp_path):
    store = Store(URL, tmp_path)
    store.save({"client_id": "x"})
    assert Store("https://other.example", tmp_path).load() == {}
    store.path.write_text("not json")
    assert store.load() == {}
    fresh = Store(URL, tmp_path / "new")
    fresh.save({"client_id": "x"})
    assert stat.S_IMODE(os.stat(tmp_path / "new").st_mode) == 0o700
