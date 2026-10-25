# Thumbly for Mac

A native SwiftUI app (macOS 14+) for the Thumbly owner API on `http://127.0.0.1:8770`. It is the owner's window onto Thumbly:
connection status, first-run setup, access requests, activity (count, details, clear),
settings with setup for each AI client, and a menu bar extra with a one-click **Pause AI control**
and **Resume**. It starts the Thumbly service when it launches, and the service stops when the app
quits.

## Build

Only Command Line Tools are needed (no Xcode project, no third-party packages).

```sh
swift build -c release            # compile
scripts/bundle.sh                 # -> build/Thumbly.app (Info.plist, icon, font, ad-hoc signature)
scripts/bundle.sh --install       # also copies to ~/Applications and registers thumbly:// with LaunchServices
open build/Thumbly.app
```

`THUMBLY_API_URL` overrides the API base (default `http://127.0.0.1:8770`).

## Test without touching the phone

```sh
python3 scripts/stub_server.py 8799 [ready|trust|devmode|unbound] &
# Drive every write (consent, revoke, pause, developer mode, diagnostics, reset, unbind) through APIClient:
THUMBLY_SELFTEST=1 THUMBLY_API_URL=http://127.0.0.1:8799 build/Thumbly.app/Contents/MacOS/Thumbly
# Render every section in light + dark to PNGs (no Screen Recording permission needed):
THUMBLY_SNAPSHOT=$PWD/build/screenshots THUMBLY_API_URL=http://127.0.0.1:8799 build/Thumbly.app/Contents/MacOS/Thumbly
open 'thumbly://access?r=h-demo'   # deep link: opens Access and highlights that request
```

The self-test refuses to run against port 8770. `GET /_log` on the stub lists every POST and its
`x-thumbly-client` header.

## Layout

| File | Role |
| --- | --- |
| `Theme.swift` | Palette (light/dark), heading font, font registration |
| `APIClient.swift` | Tolerant Codable models + async client (adds `x-thumbly-client: dashboard` to writes) |
| `AppModel.swift` | `@Observable` state, polling (2.5 s with window, 8 s menu bar only), deep links, notifications |
| `RootView.swift` | Sidebar + section routing, toast, error alert |
| `StatusView` / `SetupView` / `AccessView` / `ActivityView` / `SettingsView` / `OfflineView` / `MenuBarView` | One screen each |
| `Components.swift` | Brand mark, cards, chips, pill buttons, formatters |
| `Snapshot.swift` | Dev-only snapshot and self-test modes (env-gated) |

## Design notes

**Personality:** calm, trustworthy, a little playful: warm paper and night-ink surfaces, rounded cards,
coral for the one thing you should do next, plain-language copy ("Waiting for you", "Emergency brake").

**Palette** (`Theme.swift`, light / dark):

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| canvas | `#F7F3EC` | `#0E1326` | window background |
| surface | `#FFFFFF` | `#171E3A` | cards |
| sunken | `#EFE9DF` | `#0A0F1F` | sidebar, wells, chips |
| ink / inkSoft | `#18203D` / `#545D7C` | `#F2EEE6` / `#A6AECB` | text (AA on canvas and surface) |
| coral / coralText | `#FF7A59` / `#B3401F` | `#FF8B6B` / `#FF9C80` | primary action (ink text on fill) |
| mint / mintText | `#18A877` / `#0E7351` | `#45DDA8` / `#5FE6B6` | connected, OK |
| amber / amberText | `#F2A93B` / `#8A5600` | `#F6BC5C` / `#F6C46E` | needs attention |
| danger | `#D7373D` | `#F0555A` | Pause, destructive (white text) |

**Type:** headings use [Space Grotesk](https://github.com/floriankarsten/space-grotesk) (variable TTF from
`google/fonts/ofl/spacegrotesk`), licensed under the SIL Open Font License 1.1 — see
`Resources/OFL.txt`, shipped in the bundle as `SpaceGrotesk-OFL.txt`. It's registered at launch with
`CTFontManagerRegisterFontsForURL` (falls back to SF Rounded). Body text is SF Pro.

**Icon:** `scripts/make_icon.swift` draws it with AppKit/CoreGraphics — a rounded square with an
ink → plum → coral gradient, a tilted cream iPhone with a mint touch ripple and a peach thumb — and
builds `AppIcon.icns` with `iconutil`. The menu bar uses the SF Symbol `hand.point.up.left.fill`
(`hand.raised.fill` while paused).
