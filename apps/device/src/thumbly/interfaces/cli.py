import argparse
import asyncio
import contextlib
import json
import logging
import signal
import subprocess
import sys
from collections.abc import Generator
from pathlib import Path

import uvicorn

from thumbly.adapters.storage.activity_log import ActivityLog
from thumbly.config import Config, default_config_path, load_config
from thumbly.service import Service


class QuietServer(uvicorn.Server):
    """A second uvicorn server in the same process must leave signal handling to the first."""

    @contextlib.contextmanager
    def capture_signals(self) -> Generator[None, None, None]:
        yield


async def supervise_worker(config_path: str | None) -> None:
    """Run the CoreDevice worker as a child of this service. Restart it when it exits, with a
    capped backoff; stop it with the service."""
    delay = 1.0
    while True:
        args = [sys.executable, "-m", "thumbly.coredevice.worker"]
        if config_path:
            args += ["--config", config_path]
        process = await asyncio.create_subprocess_exec(*args)
        started = asyncio.get_running_loop().time()
        try:
            code = await process.wait()
        except asyncio.CancelledError:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), 10)
                except TimeoutError:
                    process.kill()
            raise
        if asyncio.get_running_loop().time() - started > 60:
            delay = 1.0
        logging.getLogger("thumbly").warning("device worker exited (%s); restarting in %.0fs", code, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 30)


async def follow_app(seconds: float, stop: asyncio.Event, tick: float = 2) -> None:
    """Stop the service once the Thumbly app has been gone ``seconds`` (quit, crashed or killed).
    The wait also starts at launch, so a login start without the app ends quietly."""
    from thumbly.adapters.system import launchd

    loop = asyncio.get_running_loop()
    seen = loop.time()
    while True:
        await asyncio.sleep(tick)
        if await asyncio.to_thread(launchd.app_running):
            seen = loop.time()
        elif loop.time() - seen >= seconds:
            break
    logging.getLogger("thumbly").warning("the Thumbly app is not running; stopping")
    if launchd.under_launchd():
        await asyncio.to_thread(launchd.stop_self)
    stop.set()


def listener(app, port: int, host: str = "127.0.0.1") -> QuietServer:
    return QuietServer(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            access_log=False,
            log_level="warning",
            limit_concurrency=16,
            proxy_headers=False,
        )
    )


async def serve(config, config_path: str | None = None):
    service = Service(config)
    # The MCP SDK configures the root logger at ERROR when the gateway is built; keep Thumbly's
    # own warnings (autopair, dashboard access) visible in the log.
    logging.getLogger("thumbly").setLevel(logging.INFO)
    from thumbly.interfaces.agent_gateway import create_gateway_app
    from thumbly.interfaces.dashboard import create_dashboard

    servers = []
    if config.dashboard_port:
        servers.append(listener(create_dashboard(service), config.dashboard_port))
        print(f"Owner API http://127.0.0.1:{config.dashboard_port}/api (the Thumbly app)")
    if config.local_mcp_port:
        local = f"http://127.0.0.1:{config.local_mcp_port}"
        app = create_gateway_app(service, local, allowed_hosts=[f"localhost:{config.local_mcp_port}"])
        servers.append(listener(app, config.local_mcp_port))
        print(f"MCP {local}/mcp")
    if config.public_url:
        servers.append(listener(create_gateway_app(service, config.public_url), config.mcp_port))
        print(f"Remote MCP {config.public_url}/mcp (via 127.0.0.1:{config.mcp_port})")
    if not servers:
        raise SystemExit("Nothing to serve: enable dashboard_port, local_mcp_port or public_url")
    # The phone core has no listener of its own (it is called in-process), so the service's lifecycle
    # and the stop signals are handled here rather than by one uvicorn server.
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for name in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(NotImplementedError, AttributeError):
            loop.add_signal_handler(getattr(signal, name), stop.set)
    worker = watcher = None
    if config.backend != "simulated":
        worker = asyncio.create_task(supervise_worker(config_path))
        from thumbly.adapters.system import launchd

        if config.follow_app_seconds and launchd.app_installed():
            watcher = asyncio.create_task(follow_app(config.follow_app_seconds, stop))
    await service.start()
    tasks = [asyncio.create_task(server.serve()) for server in servers]
    try:
        done, _ = await asyncio.wait(
            [asyncio.create_task(stop.wait()), *tasks], return_when=asyncio.FIRST_COMPLETED
        )
        for task in done & set(tasks):
            task.result()  # A listener that failed to start (port taken) ends the service.
    finally:
        for server in servers:
            server.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)
        await service.close()
        if watcher is not None:
            watcher.cancel()
        if worker is not None:
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker


def worker_call(config, request: dict, timeout: float) -> dict:
    from thumbly.workers.protocol import rpc

    return asyncio.run(rpc(config.runtime_dir / "device.sock", request, timeout))


def main():
    parser = argparse.ArgumentParser(description="Thumbly: let AI agents see and control your iPhone.")
    parser.add_argument("--config", default=None, help=f"Settings file (default {default_config_path()})")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("serve", help="Run Thumbly in the foreground")
    run.add_argument("--simulate", action="store_true", help="A simulated iPhone; no hardware needed")
    run.add_argument("--state-dir", type=Path, default=Path(".tmp/simulator/state"))
    run.add_argument("--runtime-dir", type=Path, default=Path(".tmp/simulator/run"))
    run.add_argument("--public-url", help="Simulator only: enable the remote MCP entry at this https origin")
    run.add_argument("--mcp-port", type=int, default=8090)
    run.add_argument("--port", type=int, default=8080, help="Simulator only: owner API port")
    run.add_argument("--local-mcp-port", type=int, default=8081, help="Simulator only: local MCP port")
    agent = commands.add_parser("agent", help="Run Thumbly in the background at login (launchd)")
    agent.add_argument("action", choices=["install", "uninstall", "status"])
    commands.add_parser("open", help="Open the Thumbly app")
    commands.add_parser("status", help="Print the iPhone connection state")
    commands.add_parser("discover", help="List USB-attached and Wi-Fi-paired iPhones")
    commands.add_parser(
        "developer-mode", help="Show the Developer Mode switch on the bound iPhone (Settings > Privacy)"
    )
    commands.add_parser("mount-ddi", help="Mount the developer disk image on the iPhone once")
    clients = commands.add_parser("clients", help="List or revoke AI clients")
    clients.add_argument("action", choices=["list", "revoke"])
    clients.add_argument("client_id", nargs="?", help="OAuth client_id to revoke")
    args = parser.parse_args()

    if args.command == "serve" and args.simulate:
        config = Config(
            backend="simulated",
            state_dir=args.state_dir,
            runtime_dir=args.runtime_dir,
            dashboard_port=args.port,
            local_mcp_port=args.local_mcp_port,
            public_url=args.public_url,
            mcp_port=args.mcp_port,
        )
        return asyncio.run(serve(config))
    if args.command == "serve" and args.public_url:
        parser.error("--public-url applies to --simulate; set public_url in the settings file")
    config = load_config(args.config)
    if args.command == "serve":
        return asyncio.run(serve(config, args.config))
    if args.command == "agent":
        from thumbly.adapters.system import launchd

        if args.action == "install":
            print(f"Installed {launchd.install(config=args.config)}")
        elif args.action == "uninstall":
            print("Removed" if launchd.uninstall() else "Not installed")
        else:
            print(json.dumps(launchd.status(), indent=2))
        return
    if args.command == "open":
        if subprocess.run(["open", "-b", "com.thumbly.mac"], check=False).returncode != 0:
            raise SystemExit("The Thumbly app is not installed. Run the installer again to add it.")
        return
    if args.command == "clients":
        from thumbly.interfaces.agent_gateway import OAuthProvider

        journal = ActivityLog(config.state_dir / "thumbly.sqlite3")
        try:
            provider = OAuthProvider(journal, "https://unused.invalid")
            if args.action == "list":
                print(json.dumps({"clients": provider.list_clients(every_issuer=True)}, indent=2))
            elif not args.client_id:
                parser.error("clients revoke requires CLIENT_ID")
            elif provider.revoke_client(args.client_id):
                print("Revoked; Thumbly drops its session within 30 seconds.")
            else:
                print("Unknown client", file=sys.stderr)
                raise SystemExit(1)
        finally:
            journal.close()
        return
    if args.command == "developer-mode":
        from thumbly.adapters.storage.pairing import PairingStore

        binding = PairingStore(config.state_dir).read()
        if binding is None:
            parser.error("Plug in your iPhone, unlock it and tap Trust first")
        request = {"op": "developer_mode", "udid": binding.udid, "owner_confirmed": True}
        print(json.dumps(worker_call(config, request, 40), indent=2))
        return
    request, timeout = {
        "status": ({"op": "status"}, 5),
        "discover": ({"op": "discover"}, 30),
        "mount-ddi": ({"op": "mount_ddi"}, 10),
    }[args.command]
    print(json.dumps(worker_call(config, request, timeout), indent=2))
