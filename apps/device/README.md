# Thumbly

This is the Thumbly service in `apps/device`; the owner uses it through the Thumbly Mac app in
[`apps/mac`](../mac/README.md). Run the Python setup and
development commands below from this directory.

Thumbly is a Mac app that lets AI agents observe and operate the owner's iPhone through
Apple's CoreDevice services: native screenshots, touch, a virtual keyboard, hardware buttons,
and app/device information. The iPhone connects to the Mac either over **USB** or over
**Wi-Fi** (after the first USB pairing); the two are interchangeable transports under one
device layer. AI agents use the MCP entries and scripts use the Python SDK
(`packages/sdk-python`) over the same MCP tools; the owner approves both in the Thumbly app.
Python, asyncio, Starlette, SQLite and pymobiledevice3; no cloud relay or persistent video.
Pairing requires accepting iOS's Trust prompt over USB. Installation and usage guides are in
[`docs/`](../../docs/index.mdx).

## Install

Requires macOS 14+, an iPhone with iOS 17.4+, and a USB data cable.

```sh
curl -fsSL https://raw.githubusercontent.com/timurcatakli/thumbly/main/install.sh | sh
# from a checkout:   sh install.sh --source /path/to/thumbly
# remove:             sh install.sh --uninstall
```

The installer installs uv if needed, runs `uv tool install ./apps/device`, builds the Mac app into
`~/Applications/Thumbly.app` (needs Command Line Tools: `xcode-select --install`), runs
`thumbly agent install` and opens the app.

`thumbly agent install` registers the per-user launchd agent `com.thumbly.agent`, which starts
at login and restarts on exit (`thumbly agent status`, `thumbly agent uninstall`). When the
Thumbly app is installed, the service follows it: once the app has been gone
`follow_app_seconds` (default 6) the service unloads itself, and the app starts it again on
launch. Set `follow_app_seconds = 0` to keep it running without the app.

| What | Where |
| --- | --- |
| Owner API for the Thumbly app (loopback only) | `http://127.0.0.1:8770/api` |
| Local MCP entry | `http://127.0.0.1:8771/mcp` |
| Logs | `~/Library/Logs/Thumbly/thumbly.log` |
| State | `~/Library/Application Support/Thumbly` |
| Optional config | `~/Library/Application Support/Thumbly/config.toml` |

No config file is needed. `config.toml` can set `public_url` for remote access; see
[`config/config.example.toml`](config/config.example.toml) for every setting.

Commands: `thumbly serve [--simulate]`, `thumbly agent install|uninstall|status`, `thumbly open`
(opens the Thumbly app), `thumbly status`, `thumbly discover`, `thumbly developer-mode`,
`thumbly mount-ddi`, `thumbly clients list|revoke`.

## Try the simulator

```sh
uv sync --frozen --dev
uv run thumbly serve --simulate
```

The owner API runs on `http://127.0.0.1:8080`; point the Mac app at it with
`THUMBLY_API_URL=http://127.0.0.1:8080` and approve clients there under Access. The local MCP entry is `http://127.0.0.1:8081/mcp`; drive the simulated phone with
the SDK (it prints an approval request; approve it under Access):

```sh
uv run python -c 'from thumbly_sdk import Phone; Phone("http://127.0.0.1:8081").home()'
```

The simulated phone's nine-target image is synthetic; simulation is not hardware validation.
Control lapses after 90 seconds without an action or screenshot (`lease_seconds`). Pause
cancels pending input, attempts release-all on a separate worker channel, and refuses
screenshots until the owner resumes (`POST /api/resume`, or an AI client the owner asked).

The MCP entries and the owner API call the phone core in-process, one bridge per caller owned
by its OAuth client (or the owner), so every entry shares the leases, the journal and
Pause. The owner API, used by the Thumbly app, is the owner surface; the MCP entries are the only
other doors.

## Device connection

```text
USB mode                          Wi-Fi mode

Mac                               Mac
 |                                 |
USB (usbmuxd, lockdown trust)    Wi-Fi (Bonjour + RemotePairing record)
 |                                 |
CoreDeviceProxy tunnel           RemotePairing tunnel
 |                                 |
RSD / RemoteXPC services  ======  RSD / RemoteXPC services
  screenshot · touch · keyboard · buttons · apps · device info
                       |
                   Thumbly
```

The worker process owns one persistent session: it discovers the bound phone, checks trust,
Developer Mode and the developer disk image (mounting it when allowed), opens the tunnel
without root, keeps the service clients open, watches for loss, and reconnects after a phone
reboot, a Wi-Fi interruption, or a USB unplug/replug. `thumbly_status` (and the Thumbly app)
reports the connection state and the reason when the owner must act. The phone stays bound
until the owner chooses **Use another iPhone** or **Reset** in the Thumbly app.

## MCP for AI clients

Agents on the Mac (Claude Code, Claude Desktop, other MCP clients) connect to
`http://127.0.0.1:8771/mcp`:

```sh
claude mcp add --transport http thumbly http://127.0.0.1:8771/mcp
```

Thumbly embeds an OAuth 2.1 authorization server. Every client is approved by the owner in
the Thumbly app under Access for a bounded duration (24 hours, 30 days, or until removed);
the consent page links to the app with `thumbly://access?r=<handle>`;
approval grants the screen and input together, and control expires with the chosen window.

Claude.ai, ChatGPT, and other hosted clients reach the Mac through a public HTTPS URL served
by Tailscale Funnel: set `public_url` in `config.toml` (e.g. `https://my-mac.tailnet.ts.net`)
and publish the remote MCP port 8090 with Funnel. The Mac keeps the MCP server, the OAuth
server, and all authorization decisions; the tunnel only carries TLS.

## Scripts

`packages/sdk-python` (`thumbly-sdk`) scripts the phone from Python:
`Phone().open_app("com.apple.mobilenotes").type("hello").home()`. The
first run asks the owner to approve it in the Thumbly app (OAuth device authorization); the
grant is cached and refreshed until the owner removes it. `scripts/bench.py` (step list) and
`scripts/endurance.py` (bounded tap/capture run with latency percentiles) are built on it.

## Thumbly app

The native SwiftUI app in [`apps/mac`](../mac/README.md) is the owner surface: status and Pause,
phone setup, AI-client approvals and revocation (Access), Activity, Settings (Use another
iPhone, Export diagnostics, Reset) and a menu bar icon. It talks to the owner API at
`http://127.0.0.1:8770/api`, which listens on loopback only, so whoever uses the Mac is the
owner. Build it with `cd apps/mac && ./scripts/bundle.sh --install`. Reset erases the pairing
(including the Wi-Fi pairing record), AI clients and activity; activity is kept 30 days.

## Development checks

For repository hook setup and contribution guidance, see the root
[CONTRIBUTING.md](../../CONTRIBUTING.md).

```sh
uv sync --frozen --dev
uv run ruff check src tests && uv run ruff format --check src tests
uv run ty check src tests
uv run pytest -q
uv build
```

CI runs the same checks on macOS with Python 3.11, 3.13, and 3.14. Type hints are gradual:
`ty` checks annotated code and infers the rest, so annotate new and touched code. The
`coredevice` worker modules import pymobiledevice3 lazily and are exempt from import
resolution where pymobiledevice3 is not installed.

## Operating limits

- Saved trust only. Pairing happens when the owner plugs the iPhone into the Mac and taps
  Trust; it also creates the Wi-Fi pairing record. Missing trust
  returns `TRUST_REQUIRED`; a missing Wi-Fi record returns `WIFI_PAIRING_REQUIRED`.
- Developer Mode must be enabled on the phone. The developer disk image is mounted
  automatically after each phone reboot when `auto_mount_ddi` is set; this needs internet
  access (about 1–4 minutes the first time). iOS 17.4 or later is required.
- Screenshots are the phone's native image; JPEG and aspect-preserving resizing are applied
  on the Mac. The phone reports no capture timestamp; source age remains unknown.
- Touch coordinates are normalized within the portrait phone image. Use portrait
  orientation lock. Taps, long presses and single-finger drags only; no multitouch.
- Printable U.S. ASCII only, up to 200 characters, through a virtual U.S. keyboard;
  unsupported text is rejected before any report. Named keys and staged
  Ctrl/Shift/Alt/Cmd chords are available. Named shortcuts require per-device
  owner-recorded validation. Hardware buttons: home, lock, volume up/down, mute, siri.
- The control lease lapses after 90 seconds without activity.
- Input is authenticated by the phone only while a media stream the worker started is
  alive; the phone shows its screen-recording indicator during input.
- A dispatched operation interrupted by cancellation or a crash is `OUTCOME_UNKNOWN`.
  It is never replayed automatically. Inspect the phone and choose a new action.
- Images and typed or sent text are never journaled or logged. The journal stores keyed
  request fingerprints and outcomes and is kept 30 days. Screenshots are transient.
- The touch, keyboard and button wire formats come from pymobiledevice3's reverse
  engineering; the pin is exact.
