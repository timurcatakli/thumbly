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
| `Theme.swift` | Palette (light/dark), type scale, font registration |
| `APIClient.swift` | Tolerant Codable models + async client (adds `x-thumbly-client: dashboard` to writes) |
| `AppModel.swift` | `@Observable` state, polling (2.5 s with window, 8 s menu bar only), deep links, notifications |
| `RootView.swift` | Sidebar + section routing, toast, error alert |
| `StatusView` / `SetupView` / `AccessView` / `ActivityView` / `SettingsView` / `OfflineView` / `MenuBarView` | One screen each |
| `Components.swift` | Brand mark, ruled cards and tables, chips, rule buttons, square toggle, formatters |
| `Snapshot.swift` | Dev-only snapshot and self-test modes (env-gated) |

## Design notes

**Personality:** "Modernist": flat, architectural, Swiss. A visible grid, zero corner radius, strong
2px rules, everything flush left, ink on off-white with one red accent. No gradients, glows or soft
shadows; alignment and divider weight do the organising. Plain-language copy ("Waiting for you",
"Emergency brake").

**Palette** (`Theme.swift`, light / dark):

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| canvas | `#F3F2F2` | `#181616` | window background |
| surface | `#EAE9E9` | `#232121` | wells, code blocks, hover fill, selected nav |
| sunken | `#EAE7E7` | `#201E1D` | sidebar |
| ink / inkSoft | `#201E1D` / ink 72% | `#F3F2F2` / ink 72% | text (AA on canvas, surface, sunken) |
| divider | ink 40% | ink 32% | 1px and 2px rules between cells and rows |
| accent | `#EC3013` | `#FF563C` | active marks, status squares, 24pt+ numerals |
| accentFill / accentPressed | `#CC2A10` / `#B82410` | `#FF563C` / `#FF9783` | fills that carry a label: buttons, count badges |
| accentText | `#AE1800` | `#FF9783` | accent as small text |
| onAccent | `#F3F2F2` | `#181616` | text on accentFill (≥ 4.5:1 in both modes) |

Status is mono: ink when all is well, accent when the owner should look (busy blinks the square).

**Type:** [Schibsted Grotesk](https://github.com/schibsted/schibsted-grotesk) for everything (variable
TTF from `google/fonts/ofl/schibstedgrotesk`), licensed under the SIL Open Font License 1.1 — see
`Resources/OFL.txt`, shipped in the bundle as `SchibstedGrotesk-OFL.txt`. It's registered at launch
with `CTFontManagerRegisterFontsForURL`, and `Theme.text` sets the `wght` axis directly (falls back to
SF Pro). Titles are ExtraBold with -0.02em tracking, kickers 11pt semibold uppercase in accentText,
numbers use tabular figures, code is SF Mono 12pt on a surface well.

**Icon:** `scripts/make_icon.swift` draws the brand mark with AppKit — flat `#EC3013` to every edge
(macOS masks it to its icon shape) with the `hand.point.up.left.fill` glyph in `#F3F2F2`, the same mark as `BrandMark` —
and builds `AppIcon.icns` with `iconutil`. `Resources/Assets/` holds PNG and PDF exports of the mark.
The menu bar uses the SF Symbol `hand.point.up.left.fill` (`hand.raised.fill` while paused).
