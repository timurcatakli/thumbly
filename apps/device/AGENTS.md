# Thumbly project context

Thumbly lets AI agents see and control an iPhone from a Mac. Use `Thumbly` in prose, `thumbly`
for packages/commands/paths, and `THUMBLY` for environment-variable prefixes.

`apps/device` is the Python service: an owner-bound iPhone observed and driven through Apple
CoreDevice services (screenshots, touch, keyboard, hardware buttons, app and device
information) over a USB or Wi-Fi transport. It runs as a per-user launchd agent and serves:

- the dashboard (`apps/dashboard`, built into `src/thumbly/web/dashboard`) on
  http://127.0.0.1:8770, loopback only; whoever uses the Mac is the owner;
- the MCP entry for agents on this Mac on http://127.0.0.1:8771/mcp (OAuth 2.1; the owner
  approves every client in the dashboard);
- optionally a remote MCP entry behind Tailscale Funnel (`public_url`).

The device layer lives in `src/thumbly/coredevice` and runs as a child worker process. Preserve
owner binding, authentication, control leases, explicit pairing requirements, and the
transport-agnostic device port when making changes. pymobiledevice3 is pinned exactly; do not
bump it without hardware validation. The `/api/*` routes are the contract between the service
and the dashboard; change both sides together.

Development checks (run from `apps/device`):

```sh
uv sync --frozen --dev
uv run --frozen ruff check src tests
uv run --frozen ruff format --check src tests
uv run --frozen ty check src tests
uv run --frozen pytest -q
uv build
```
