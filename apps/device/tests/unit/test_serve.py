"""``thumbly serve``: the dashboard and the local MCP entry live as long as the service."""

import asyncio
import os
import signal
import socket

import httpx
import pytest

from thumbly.config import Config
from thumbly.interfaces import cli


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_nothing_to_serve_is_an_error(tmp_path):
    config = Config(
        backend="simulated",
        state_dir=tmp_path / "s",
        runtime_dir=tmp_path / "r",
        dashboard_port=0,
        local_mcp_port=0,
    )
    with pytest.raises(SystemExit, match="Nothing to serve"):
        await cli.serve(config)


async def test_dashboard_and_local_mcp_then_a_signal_stops_everything(tmp_path):
    port, mcp_port = free_port(), free_port()
    config = Config(
        backend="simulated",
        state_dir=tmp_path / "s",
        runtime_dir=tmp_path / "r",
        dashboard_port=port,
        local_mcp_port=mcp_port,
    )
    task = asyncio.create_task(cli.serve(config))
    async with httpx.AsyncClient() as c:
        for _ in range(100):
            try:
                r = await c.get(f"http://127.0.0.1:{port}/api/overview")
                break
            except httpx.ConnectError:
                await asyncio.sleep(0.05)
        assert r.status_code == 200 and r.json()["name"] == "Thumbly"
        metadata = await c.get(f"http://127.0.0.1:{mcp_port}/.well-known/oauth-authorization-server")
        assert metadata.json()["issuer"] == f"http://127.0.0.1:{mcp_port}"
        assert (await c.post(f"http://127.0.0.1:{mcp_port}/mcp", json={})).status_code == 401
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, 10)


def test_simulate_flags_parse(tmp_path, monkeypatch):
    seen = {}

    async def fake_serve(config, config_path=None):
        seen["config"] = config

    monkeypatch.setattr(cli, "serve", fake_serve)
    monkeypatch.setattr(
        "sys.argv",
        [
            "thumbly",
            "serve",
            "--simulate",
            "--state-dir",
            str(tmp_path / "s"),
            "--runtime-dir",
            str(tmp_path / "r"),
        ],
    )
    cli.main()
    assert seen["config"].backend == "simulated" and seen["config"].runtime_dir == tmp_path / "r"


async def test_the_service_stops_once_the_app_is_gone(monkeypatch):
    from thumbly.adapters.system import launchd

    running = [True, True, False, False, False]
    stopped = []
    monkeypatch.setattr(launchd, "app_running", lambda: running.pop(0))
    monkeypatch.setattr(launchd, "under_launchd", lambda: True)
    monkeypatch.setattr(launchd, "stop_self", lambda: stopped.append(True))
    stop = asyncio.Event()
    await asyncio.wait_for(cli.follow_app(0.025, stop, tick=0.01), 2)
    assert stop.is_set() and stopped == [True]
    assert running == []  # Kept running while the app was up, then waited out the grace.
