import os
import tomllib
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from thumbly.domain.models import StrictModel

HOME = Path.home() / "Library" / "Application Support" / "Thumbly"
DASHBOARD_PORT = 8770
LOCAL_MCP_PORT = 8771


class Config(StrictModel):
    backend: Literal["simulated", "hardware"] = "hardware"
    state_dir: Path = HOME
    runtime_dir: Path = HOME / "run"
    # Seconds without owner activity after which the control lease lapses and held input is released.
    lease_seconds: float = Field(default=90, ge=1, le=600)
    queue_size: int = Field(default=16, ge=1, le=64)
    frame_ttl: float = Field(default=120, ge=1, le=300)
    capture_deadline: float = Field(default=20, ge=0.1, le=60)
    # CoreDevice worker tuning. Transport (USB or Wi-Fi) is part of the owner binding, not config.
    auto_mount_ddi: bool = True
    ddi_mount_deadline: float = Field(default=240, ge=10, le=900)
    discovery_seconds: float = Field(default=3, ge=0.5, le=15)  # One Bonjour browse (re-asks each second).
    # The worker looks for the phone inside bounded windows opened by events (start, cable
    # pulled, Wi-Fi session lost, a request that needs the phone). A connection that starts
    # inside the window may finish after it.
    search_window_seconds: float = Field(default=10, ge=1, le=60)
    # Wi-Fi only: check the session after this long without a request. USB needs no probe; the
    # cable and the tunnel report their own loss.
    probe_interval: float = Field(default=30, ge=1, le=300)
    reconnect_min_seconds: float = Field(default=1, ge=0.01, le=60)
    reconnect_max_seconds: float = Field(default=15, ge=0.01, le=300)
    recover_wait_seconds: float = Field(default=8, ge=0, le=60)
    # How long a request waits for the session to come back while the worker is switching
    # transports or reconnecting right after a loss, instead of failing at once.
    switch_hold_seconds: float = Field(default=8, ge=0, le=60)
    hid_idle_seconds: float = Field(default=20, ge=1, le=600)
    hid_refresh_seconds: float = Field(default=15, ge=1, le=600)
    stream_start_seconds: float = Field(default=3, ge=0.2, le=15)  # Wait for the first decoded frame.
    stream_frame_ttl: float = Field(default=1.0, ge=0.1, le=10)  # Refuse a decoded frame older than this.
    stream_jpeg_quality: int = Field(default=60, ge=1, le=95)  # JPEG quality for streamed frames.
    # Owner API for the Thumbly app on http://127.0.0.1:<port>. Whoever uses this Mac is the owner.
    # 0 disables it.
    dashboard_port: int = Field(default=DASHBOARD_PORT, ge=0, le=65535)
    # MCP for agents on this Mac (Claude Code, Claude Desktop): http://127.0.0.1:<port>/mcp. OAuth
    # allows plain HTTP on loopback; the owner approves every client in the Thumbly app. 0 = disabled.
    local_mcp_port: int = Field(default=LOCAL_MCP_PORT, ge=0, le=65535)
    # Public HTTPS origin for cloud AI clients, e.g. "https://my-mac.tailnet.ts.net" (Tailscale
    # Funnel forwards it to 127.0.0.1:<mcp_port>). Unset = disabled.
    public_url: str | None = None
    mcp_port: int = Field(default=8090, ge=1024, le=65535)
    # Bind the single cabled iPhone whenever none is bound; iOS still asks the owner to tap Trust.
    auto_pair: bool = True
    # When the Thumbly app is installed, run only while it runs: the service stops itself (and with
    # it every MCP entry) once the app has been gone this many seconds. The app starts it again on
    # launch. 0 = run regardless of the app.
    follow_app_seconds: float = Field(default=6, ge=0, le=600)

    @model_validator(mode="after")
    def local(self):
        ports = [self.dashboard_port, self.local_mcp_port, self.mcp_port if self.public_url else 0]
        used = [port for port in ports if port]
        if len(used) != len(set(used)):
            raise ValueError("dashboard_port, local_mcp_port and mcp_port must differ")
        if self.reconnect_max_seconds < self.reconnect_min_seconds:
            raise ValueError("reconnect_max_seconds must be at least reconnect_min_seconds")
        if self.public_url is not None:
            self.public_url = public_origin(self.public_url)
        return self


def public_origin(value: str) -> str:
    """Accept only a bare HTTPS origin; the tunnel terminates TLS and forwards to the MCP listener."""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("public_url must be a bare https origin such as https://my-mac.tailnet.ts.net")
    port = f":{parsed.port}" if parsed.port not in {None, 443} else ""
    return f"https://{parsed.hostname.lower()}{port}"


def default_config_path() -> Path:
    return Path(os.environ.get("THUMBLY_CONFIG") or HOME / "config.toml")


def load_config(path: str | Path | None = None) -> Config:
    """Read config.toml; the file is optional and every setting has a default."""
    path = Path(path) if path is not None else default_config_path()
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        data = {}
    return Config.model_validate(data)
