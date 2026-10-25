# Thumbly

Thumbly is a Mac app that lets AI agents see and control your iPhone. It connects to the
phone over USB (or Wi-Fi after the first USB pairing) through Apple's CoreDevice services:
native screenshots, touch, a virtual keyboard, hardware buttons, and app and device
information. Agents connect over MCP; you approve every client and keep control from the
Thumbly app on your Mac.

## Requirements

- macOS 14 or later
- iPhone with iOS 17.4 or later
- A USB data cable

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/timurcatakli/thumbly/main/install.sh | sh
```

This installs [uv](https://docs.astral.sh/uv/) if needed, installs the Thumbly service with
`uv tool install`, builds the Thumbly Mac app into `~/Applications/Thumbly.app` (needs Apple's
Command Line Tools: `xcode-select --install`), registers the service with launchd
(`thumbly agent install`) and opens the app. With the app installed, the service and its MCP entry
run only while the app is open: quitting the app stops them, opening it starts them again. From a checkout, run `sh install.sh --source .`;
to remove Thumbly, run `sh install.sh --uninstall`. See [Install Thumbly](docs/install/install-thumbly.mdx).

## Connect your iPhone

1. Plug the iPhone into the Mac with a data cable, unlock it and tap **Trust**. Thumbly binds it.
2. Turn on **Settings → Privacy & Security → Developer Mode** and restart the iPhone. If the
   option is hidden, the Thumbly app reveals it (**Show the switch**).
3. Thumbly installs Apple's developer disk image automatically (needs internet; about 1–4 minutes
   the first time).

## Connect Claude Code

```sh
claude mcp add --transport http thumbly http://127.0.0.1:8771/mcp
```

In Claude Code, run `/mcp` → **thumbly** → **Authenticate**, click **Open Thumbly** on the page
that opens, then allow the request in the Thumbly app under **Access**. Claude Desktop and other MCP clients use the same URL. For Claude.ai
or ChatGPT, see [remote access](docs/remote-access/index.mdx).

The full guide is in [`docs/`](docs/index.mdx); product details and operating limits are in the
[device README](apps/device/README.md).

## Repository

```
thumbly/
├── apps/
│   ├── device/      # The Thumbly service: Python, uv
│   └── mac/         # The Thumbly Mac app: SwiftUI
├── packages/
│   └── sdk-python/  # thumbly-sdk: script the iPhone from Python
└── docs/            # The user guide (MDX)
```

## Develop

Python (the Thumbly service), run from `apps/device`:

```sh
uv sync --frozen --dev
uv run thumbly serve --simulate      # simulated iPhone: owner API :8080, MCP :8081
uv run pytest -q
```

Mac app, run from `apps/mac` (Command Line Tools only, no Xcode project):

```sh
swift build -c release
./scripts/bundle.sh --install        # -> ~/Applications/Thumbly.app
THUMBLY_API_URL=http://127.0.0.1:8080 ~/Applications/Thumbly.app/Contents/MacOS/Thumbly  # against the simulator
```

Hooks: `uv run --project apps/device pre-commit install`. More in the
[Mac app README](apps/mac/README.md).

## Contributing

Read [CONTRIBUTING.md](CONTRIBUTING.md) and open a pull request.

## License

Apache-2.0 for this repository ([LICENSE](LICENSE)), except `apps/device`, which is
GPL-3.0 ([apps/device/LICENSE](apps/device/LICENSE)) because it links to
[pymobiledevice3](https://github.com/doronz88/pymobiledevice3). See [NOTICE](NOTICE) and
[THIRD_PARTY_NOTICES.md](apps/device/THIRD_PARTY_NOTICES.md).
