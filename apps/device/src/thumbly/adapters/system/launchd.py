"""Thumbly as a per-user launchd agent: started at login, restarted when it exits.

A factory reset relies on the restart: the service erases its state and stops itself. When the
Thumbly app is installed the service follows it instead (``follow_app_seconds``): it unloads itself
once the app is gone, and the app loads it again on launch.
"""

import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

LABEL = "com.thumbly.agent"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOGS = Path.home() / "Library" / "Logs" / "Thumbly"
BOOTSTRAP_TRIES = 15


def agent_plist(program: str, config: str | None = None) -> dict:
    arguments = [program, "serve"] if config is None else [program, "--config", config, "serve"]
    return {
        "Label": LABEL,
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 5,
        "ProcessType": "Interactive",  # Screen and input latency matter more than battery here.
        "StandardOutPath": str(LOGS / "thumbly.log"),
        "StandardErrorPath": str(LOGS / "thumbly.log"),
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install(program: str | None = None, config: str | None = None) -> Path:
    """Write the agent and (re)start it. ``program`` defaults to this environment's thumbly."""
    program = program or str(Path(sys.executable).with_name("thumbly"))
    LOGS.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    _launchctl("bootout", f"{_domain()}/{LABEL}")
    PLIST.write_bytes(plistlib.dumps(agent_plist(program, config)))
    # bootout returns before the old service has exited; bootstrap fails (error 5) until it has.
    for _ in range(BOOTSTRAP_TRIES):
        result = _launchctl("bootstrap", _domain(), str(PLIST))
        if result.returncode == 0:
            return PLIST
        time.sleep(1)
    raise RuntimeError(f"launchctl bootstrap failed: {result.stderr.strip() or result.returncode}")


def uninstall() -> bool:
    """Stop the agent and remove it; state under Application Support is kept."""
    _launchctl("bootout", f"{_domain()}/{LABEL}")
    if PLIST.exists():
        PLIST.unlink()
        return True
    return False


APPS = (Path.home() / "Applications" / "Thumbly.app", Path("/Applications/Thumbly.app"))


def app_installed() -> bool:
    return any(path.exists() for path in APPS)


def app_running() -> bool:
    result = subprocess.run(
        ["pgrep", "-f", "Thumbly.app/Contents/MacOS/Thumbly"], capture_output=True, check=False
    )
    return result.returncode == 0


def under_launchd() -> bool:
    return os.environ.get("XPC_SERVICE_NAME") == LABEL


def stop_self() -> None:
    """Unload this agent so launchd does not restart it; it sends SIGTERM, which stops the service
    cleanly. The plist stays, so the next login (or the app) loads it again."""
    _launchctl("bootout", f"{_domain()}/{LABEL}")


def status() -> dict:
    result = _launchctl("print", f"{_domain()}/{LABEL}")
    state, pid = None, None
    for line in result.stdout.splitlines():
        key, _, value = line.strip().partition(" = ")
        if key == "state":
            state = value
        elif key == "pid":
            pid = int(value) if value.isdigit() else None
    return {
        "installed": PLIST.exists(),
        "loaded": result.returncode == 0,
        "state": state,
        "pid": pid,
        "log": str(LOGS / "thumbly.log"),
    }
