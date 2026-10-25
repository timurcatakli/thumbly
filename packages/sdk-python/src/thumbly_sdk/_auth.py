"""Grant cache, refresh and first-run approval (OAuth device authorization grant, RFC 8628).

One JSON file per Thumbly address under the config directory holds the client id and the token
pair; it is private to the user (0600 in a 0700 directory) and replaced atomically. Refresh and
approval run under an exclusive lock on a sidecar file: Thumbly rotates refresh tokens and treats a reused one as stolen, so two processes refreshing at
once would end the grant for both.
"""

import contextlib
import hashlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import httpx

from thumbly_sdk.errors import ApprovalDenied, ApprovalExpired, ApprovalRequired, ThumblyError

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SCOPE = "phone:read phone:control"
EARLY_REFRESH = 60  # Refresh an access token this many seconds before it ends.
MIN_INTERVAL, MAX_INTERVAL = 0.1, 60.0
CACHE_VERSION = 1


def config_dir() -> Path:
    base = os.environ.get("THUMBLY_CONFIG_DIR")
    if base:
        return Path(base)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "thumbly" / "sdk"


@contextlib.contextmanager
def exclusive(path: Path) -> Generator[None]:
    """Process-wide exclusive lock on ``path`` (created 0600)."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
            import msvcrt

            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.1)
            try:
                yield
            finally:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class Store:
    """The cache file of one Thumbly address, keyed by its base URL."""

    def __init__(self, base_url: str, directory: Path | None = None):
        self.directory = Path(directory) if directory else config_dir()
        key = hashlib.sha256(base_url.encode()).hexdigest()[:16]
        self.path = self.directory / f"{key}.json"
        self.lock_path = self.directory / f"{key}.lock"
        self.base_url = base_url

    def ensure_directory(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        if (
            not isinstance(data, dict)
            or data.get("version") != CACHE_VERSION
            or data.get("url") != self.base_url
        ):
            return {}
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.ensure_directory()
        payload = json.dumps({**data, "version": CACHE_VERSION, "url": self.base_url}, indent=2).encode()
        fd, tmp = tempfile.mkstemp(dir=self.directory, prefix=".tmp-", suffix=".json")  # Mode 0600.
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(tmp, self.path)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    @contextlib.contextmanager
    def locked(self) -> Generator[None]:
        self.ensure_directory()
        with exclusive(self.lock_path):
            yield


def _form_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"http_{response.status_code}"
    return str(data.get("error") or f"http_{response.status_code}") if isinstance(data, dict) else "invalid"


class Auth:
    """Keeps a valid access token for one Thumbly address, approving on first use when allowed."""

    def __init__(
        self,
        http: httpx.Client,
        store: Store,
        *,
        client_name: str,
        allow_approval: bool,
        approve_timeout: float,
        notify: Callable[[str], None],
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.http, self.store = http, store
        self.client_name = client_name
        self.allow_approval = allow_approval
        self.approve_timeout = approve_timeout
        self.notify = notify
        self.clock, self.sleep = clock, sleep
        self.data = store.load()
        self.metadata: dict[str, Any] | None = None

    # -- public --------------------------------------------------------------------------

    def token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh and self._fresh(self.data):
            return self.data["access_token"]
        stale = self.data.get("access_token")
        with self.store.locked():
            # Another process may have refreshed or approved while this one waited.
            self.data = self.store.load() or self.data
            if self._fresh(self.data) and not (force_refresh and self.data.get("access_token") == stale):
                return self.data["access_token"]
            if self.data.get("refresh_token") and self._refresh():
                return self.data["access_token"]
            self._approve()
            return self.data["access_token"]

    # -- internals -----------------------------------------------------------------------

    def _fresh(self, data: dict) -> bool:
        return (
            bool(data.get("access_token"))
            and float(data.get("access_expires", 0)) - EARLY_REFRESH > self.clock()
        )

    def _discover(self) -> dict[str, Any]:
        if self.metadata is None:
            r = self.http.get("/.well-known/oauth-authorization-server")
            if r.status_code != 200:
                raise ThumblyError("DISCOVERY_FAILED", f"Thumbly answered {r.status_code} to discovery")
            metadata = r.json()
            if not isinstance(metadata, dict) or "device_authorization_endpoint" not in metadata:
                raise ThumblyError("SERVER_TOO_OLD", "this Thumbly does not offer script approval; update it")
            self.metadata = metadata
        return self.metadata

    def _endpoint(self, name: str) -> str:
        return str(self._discover()[name])

    def _keep(self, issued: dict, now: float) -> None:
        self.data = {
            **self.data,
            "access_token": issued["access_token"],
            "access_expires": now + float(issued.get("expires_in", 900)),
            "refresh_token": issued.get("refresh_token", self.data.get("refresh_token")),
            "scope": issued.get("scope", SCOPE),
        }
        self.store.save(self.data)

    def _refresh(self) -> bool:
        """True with a new token pair; False when the grant is gone and approval is needed."""
        now = self.clock()
        r = self.http.post(
            self._endpoint("token_endpoint"),
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.data["refresh_token"],
                "client_id": self.data.get("client_id", ""),
            },
        )
        if r.status_code == 200:
            self._keep(r.json(), now)
            return True
        if r.status_code in (400, 401):  # invalid_grant / invalid_client: revoked, expired, Thumbly reset.
            self.data = {k: v for k, v in self.data.items() if k not in ("access_token", "refresh_token")}
            self.store.save(self.data)
            return False
        raise ThumblyError("REFRESH_FAILED", f"Thumbly answered {r.status_code} to a token refresh")

    def _approve(self) -> None:
        if not self.allow_approval:
            raise ApprovalRequired(
                "APPROVAL_REQUIRED",
                "no valid grant for Thumbly; run once in a terminal (or with allow_approval=True) "
                "and approve it in the Thumbly app (Access)",
            )
        started = self.clock()
        form = {"client_name": self.client_name, "scope": SCOPE}
        if self.data.get("client_id"):
            form["client_id"] = self.data["client_id"]
        r = self.http.post(self._endpoint("device_authorization_endpoint"), data=form)
        if r.status_code != 200:
            raise ThumblyError("APPROVAL_UNAVAILABLE", f"device authorization refused: {_form_error(r)}")
        grant = r.json()
        client_id = grant["client_id"]
        lines = [
            f"Thumbly: approve '{self.client_name}' in the Thumbly app (Access). Code {grant['user_code']}.",
            f"  {grant.get('verification_uri_complete') or grant.get('verification_uri')}",
            "  For unattended scripts choose 'Until I remove it'.",
        ]
        self.notify("\n".join(lines))
        interval = min(max(float(grant.get("interval", 5)), MIN_INTERVAL), MAX_INTERVAL)
        deadline = started + min(float(grant.get("expires_in", 600)), self.approve_timeout)
        while True:
            if self.clock() + interval > deadline:
                raise ApprovalExpired("APPROVAL_EXPIRED", "no decision in the Thumbly app in time; run again")
            self.sleep(interval)
            now = self.clock()
            r = self.http.post(
                self._endpoint("token_endpoint"),
                data={
                    "grant_type": DEVICE_GRANT,
                    "device_code": grant["device_code"],
                    "client_id": client_id,
                },
            )
            if r.status_code == 200:
                self.data = {
                    **self.data,
                    "client_id": client_id,
                    "client_name": self.client_name,
                    "issuer": self._discover().get("issuer"),
                }
                self._keep(r.json(), now)
                self.notify("Thumbly: approved.")
                return
            error = _form_error(r)
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval = min(interval + 5, MAX_INTERVAL)
                continue
            if error == "access_denied":
                raise ApprovalDenied("APPROVAL_DENIED", "the owner denied this request in the Thumbly app")
            if error in ("expired_token", "invalid_grant"):
                raise ApprovalExpired("APPROVAL_EXPIRED", "the request expired before a decision; run again")
            raise ThumblyError("APPROVAL_FAILED", f"Thumbly answered {error}")
