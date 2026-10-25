# Contributing to Thumbly

Thank you for taking the time to contribute to Thumbly. Please read this guide
before opening a pull request. Contributions are welcome as pull requests against `main`.

> **Important:** We only accept pull requests that address a single issue. Please do
> not submit pull requests containing multiple unrelated fixes or features. If you have
> multiple contributions, open a separate pull request for each one.

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md). Report security
problems privately as described in the [security policy](SECURITY.md), never in a public
issue or pull request.

## Developing

The development branch is `main`. All pull requests are made against `main`.

### Prerequisites

- A Mac with macOS 14 or later (an iPhone is optional: `thumbly serve --simulate` needs none)
- [uv](https://docs.astral.sh/uv/) 0.12 or newer
- Python 3.11, 3.13, or 3.14
- Apple's Xcode Command Line Tools (`xcode-select --install`) for the Mac app in `apps/mac`

### Setup

```sh
uv sync --project apps/device --frozen --dev
uv run --project apps/device pre-commit install  # Once per clone; runs ruff and ty on staged files at each commit.
```

### Checks

Run the same checks as CI before you push:

```sh
cd apps/device
uv run --frozen ruff check src tests
uv run --frozen ruff format --check src tests
uv run --frozen ty check src tests
uv run --frozen pytest -q
uv build
```

The Mac app (`apps/mac`) builds with the Command Line Tools:

```sh
cd apps/mac
swift build -c release
./scripts/bundle.sh
```

Type hints are gradual: `ty` checks annotated code and infers the rest, so annotate
new and touched code.

### Project boundaries

Read [`apps/device/AGENTS.md`](apps/device/AGENTS.md) before changing behavior. Preserve
owner binding, authentication, control leases, and explicit pairing requirements. Do not
add cloud orchestration or persistent video without an accepted design discussion. The
loopback-only owner API (`http://127.0.0.1:8770/api`) that the Thumbly Mac app uses is the owner
trust boundary; a change to it needs a design discussion before code.

## Pull request workflow

1. Open or find an issue describing the change. Reference it with `Closes #<issue>`.
2. Keep the pull request focused on that one issue.
3. Fill in the pull request template, including how you tested the change.
4. Make sure CI passes. A maintainer reviews once checks are green.
5. The maintainer squash-merges the pull request, so its title becomes the commit message
   on `main`. Write the title as one clear sentence.

## Reporting issues

Use the issue templates for bug reports and feature requests. Do not include
credentials, device identifiers, screenshots of private data, or typed content in
issues.
