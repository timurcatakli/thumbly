# thumbly-sdk

Script the iPhone connected to Thumbly from Python.

```python
from thumbly_sdk import Phone

with Phone() as phone:                           # Thumbly on this Mac: http://127.0.0.1:8771
    phone.apps("notes")                          # [{"bundle_id": "com.apple.mobilenotes", ...}]
    phone.open_app("com.apple.mobilenotes").type("hello").home()
    frame = phone.screenshot()
    frame.save("screen.jpg")
    frame.tap(200, 400)                          # pixels of that image
    phone.tap(0.5, 0.9)                          # or fractions of the screen
```

With no address the SDK talks to Thumbly on the same Mac at `http://127.0.0.1:8771`. Pass
`http://127.0.0.1:<port>` or `http://localhost:<port>` for another local port, or a full
`https://` origin (for example `Phone("https://<mac>.ts.net")` through Tailscale Funnel) for
remote access. Plain `http://` is refused for anything but loopback. `examples/open_youtube.py`
is a complete script.

## First run: approve in the Thumbly app

The first call asks Thumbly for access and prints the client name, a code such as `BCDF-GHJK`,
and a `thumbly://access?r=…` link. For unattended scripts choose **Until I remove it**.

Open the link (or the Thumbly app on the Mac), go to **Access**, check the code, and choose
**Allow for 24 hours**, **30 days** (renewed while used) or **Until I remove it**; **Deny**
refuses it. Scripts get screen and input access, never Thumbly settings.

The grant is cached in `~/.config/thumbly/sdk/` (one file per Thumbly address, mode 0600 in a
0700 directory) and refreshed without asking again. Without a terminal (cron, CI) the SDK never
waits for approval: it raises `ApprovalRequired`. Run once by hand, or use `thumbly-sdk login`.

## Security

- **Local** (`http://127.0.0.1:8771`): plain HTTP on loopback only; traffic never leaves the
  Mac, and the SDK ignores proxy settings for it.
- **Remote** (`https://….ts.net`): normal certificate verification against the system's
  trusted certificates.
- The SDK calls the same MCP tools AI agents use, so control leases, pause, the activity journal,
  and removal under Access apply to scripts as well.

## Behaviour worth knowing

- **Touches refer to a frame.** Every action returns a fresh screenshot by default
  (`observe=True`), and the next touch refers to it. If the screen was captured more than 120 s
  ago or another client captured since, Thumbly refuses with `FrameExpired`; take a
  `screenshot()` and decide again. The SDK never re-taps on its own.
- **`type(text)`** types short printable ASCII key by key; anything longer or non-ASCII is pasted
  through the phone clipboard (`paste`), which replaces the clipboard.
- **Retries.** A dropped connection retries the call once with the same operation id; Thumbly
  runs each id at most once. If that fails too, the SDK reads the journal and raises
  `OutcomeUnknown`: check the screen, do not replay.
- **Rate limit.** 120 calls a minute; `RATE_LIMITED` is retried after 1, 2 and 4 s.
- **Control.** The first action takes control; it lapses after 90 s without activity.
  `close()` (or leaving the `with` block) releases it. `ControlBusy` means another client has it.

## Command line

```
thumbly-sdk login
thumbly-sdk status
thumbly-sdk screenshot screen.png
thumbly-sdk call thumbly_button button=home observe=false
thumbly-sdk status https://<mac>.ts.net
```

Every command takes an optional Thumbly address first (default `http://127.0.0.1:8771`). `call`
takes any MCP tool name and prints its JSON result, so any language can drive the phone
through this command. The wire protocol itself is documented in [docs/reference/sdk-protocol.mdx](../../docs/reference/sdk-protocol.mdx).
