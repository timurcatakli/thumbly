# Thumbly

Thumbly lets AI agents see and control an iPhone from a Mac. Two parts:

- `apps/device`: the Python service (launchd agent, iPhone worker, MCP, owner API). Read
  `apps/device/AGENTS.md` before changing it.
- `apps/mac`: the SwiftUI Mac app, the owner's window onto the service. Build with
  `cd apps/mac && ./scripts/bundle.sh`.

`packages/sdk-python` is the Python SDK for scripts. `install.sh` installs everything.
