"""The script SDK against the real local gateway on loopback: approval, actions, revocation."""

import asyncio
import json
import socket
import stat

import pytest
import uvicorn
from thumbly_sdk import ApprovalRequired, FrameExpired, Phone

from thumbly.config import Config
from thumbly.interfaces import device_grant
from thumbly.interfaces.agent_gateway import create_gateway_app, grant_from_choice
from thumbly.interfaces.cli import QuietServer
from thumbly.service import Service


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Counter:
    """Counts HTTP requests that reached the app, by path."""

    def __init__(self, app):
        self.app, self.paths = app, []

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            self.paths.append(scope["path"])
        await self.app(scope, receive, send)


@pytest.fixture
async def thumbly(tmp_path, monkeypatch):
    monkeypatch.setattr(device_grant, "INTERVAL", 0.2)
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    config = Config(
        backend="simulated",
        state_dir=tmp_path / "state",
        runtime_dir=tmp_path / "run",
        local_mcp_port=port,
    )
    service = Service(config)
    await service.start()
    counter = Counter(create_gateway_app(service, url))
    server = QuietServer(
        uvicorn.Config(counter, host="127.0.0.1", port=port, access_log=False, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.05)
    assert server.started
    try:
        yield service, url, counter
    finally:
        server.should_exit = True
        await task
        await service.close()


async def approver(service, window="never"):
    """The owner clicking Approve in the Thumbly app for every script request."""
    provider = service.auth_provider
    while True:
        for row in provider.list_pending():
            granted, seconds = grant_from_choice(row["scopes"], window)
            provider.decide_handle(row["handle"], granted, seconds)
        await asyncio.sleep(0.05)


def phone(url, cache, **options):
    options.setdefault("allow_approval", True)
    return Phone(url, cache_dir=cache, name="e2e.py", notify=lambda text: None, approve_timeout=20, **options)


async def test_script_approved_once_then_acts_and_loses_access_on_removal(thumbly, tmp_path):
    service, url, counter = thumbly
    cache = tmp_path / "sdk"
    owner = asyncio.create_task(approver(service))
    try:

        def first_run():
            with phone(url, cache) as p:
                p.open_app("com.apple.mobilenotes").type("hello").tap(0.5, 0.5).swipe(
                    0.5, 0.8, 0.5, 0.2, hold=0.2
                ).home()
                return p.frame, p.auth.data["client_id"]

        frame, client_id = await asyncio.to_thread(first_run)
    finally:
        owner.cancel()
    assert frame is not None and frame.image
    assert ("launch_app", "com.apple.mobilenotes") in service.phone.device.reports
    assert counter.paths.count("/oauth/device_authorization") == 1

    (cached,) = cache.glob("*.json")
    assert stat.S_IMODE(cached.stat().st_mode) == 0o600
    assert "fingerprint" not in json.loads(cached.read_text())
    (client,) = [
        c for c in service.auth_provider.list_clients(every_issuer=True) if c["client_id"] == client_id
    ]
    assert client["never_expires"] and client["client_name"] == "e2e.py"

    # Second run: no approval, no device request; a stale frame is refused, never re-tapped.
    def second_run():
        with phone(url, cache, allow_approval=False) as p:
            p.screenshot()
            frames = service.phone.recent_frames
            frames.update({k: (epoch, seen - 1000) for k, (epoch, seen) in frames.items()})
            before = len(service.phone.device.reports)
            with pytest.raises(FrameExpired) as info:
                p.tap(0.5, 0.5)
            assert info.value.dispatched is False
            return before, len(service.phone.device.reports)

    before, after = await asyncio.to_thread(second_run)
    assert after == before
    assert counter.paths.count("/oauth/device_authorization") == 1

    # The owner removes the script: a headless run fails fast instead of waiting for approval.
    assert service.auth_provider.revoke_client(client_id)

    def after_removal():
        with phone(url, cache, allow_approval=False) as p:
            p.status()

    with pytest.raises(ApprovalRequired):
        await asyncio.to_thread(after_removal)
