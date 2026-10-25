# Changelog

All notable changes to Thumbly are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and Thumbly uses
[Semantic Versioning](https://semver.org/). The version lives in [`VERSION`](VERSION) and is
shared by the service, the Mac app and the docs.

## [Unreleased]

## [0.1.0] - 2026-10-24

First public release.

### Added

- Thumbly service (`apps/device`): launchd agent, CoreDevice connection over USB or Wi-Fi,
  native screenshots, touch, keyboard, hardware buttons, and app and device information.
- MCP server at `http://127.0.0.1:8771/mcp`; you approve each client in the Mac app.
- Thumbly Mac app (`apps/mac`): status, access approvals, activity log, settings, a menu bar
  panel and an emergency pause. The app version is shown in the sidebar footer.
- Python SDK `thumbly-sdk` (`packages/sdk-python`).
- Simulated iPhone (`thumbly serve --simulate`) for development without a device.
- One-line installer (`install.sh`).

[Unreleased]: https://github.com/timurcatakli/thumbly/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/timurcatakli/thumbly/releases/tag/v0.1.0
