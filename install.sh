#!/bin/sh
# Install Thumbly on this Mac: the background service, the Mac app, and login start-up.
#
#   curl -fsSL https://raw.githubusercontent.com/timurcatakli/thumbly/main/install.sh | sh
#   sh install.sh --source /path/to/thumbly     # install from a local checkout
#   sh install.sh --uninstall                   # remove Thumbly (keeps your settings and pairing)
#
# Needs macOS 14+, internet access, and Apple's Command Line Tools (for the Mac app).
set -eu

REPO=${THUMBLY_REPO:-https://github.com/timurcatakli/thumbly.git}
SOURCE=""
UNINSTALL=0
APP="$HOME/Applications/Thumbly.app"

while [ $# -gt 0 ]; do
  case "$1" in
    --source) SOURCE=$2; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1m==>\033[0m %s\n' "$1"; }
fail() { printf 'Error: %s\n' "$1" >&2; exit 1; }

[ "$(uname -s)" = Darwin ] || fail "Thumbly runs on macOS only."
major=$(sw_vers -productVersion | cut -d. -f1)
[ "$major" -ge 14 ] || fail "Thumbly needs macOS 14 or later (this Mac has $(sw_vers -productVersion))."

if [ "$UNINSTALL" = 1 ]; then
  say "Stopping Thumbly"
  command -v thumbly >/dev/null 2>&1 && thumbly agent uninstall || true
  command -v uv >/dev/null 2>&1 && uv tool uninstall thumbly || true
  rm -rf "$APP"
  say "Removed. Settings and pairing stay in ~/Library/Application Support/Thumbly (delete it to forget everything)."
  exit 0
fi

if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
  say "Installing uv (Python package manager)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
PATH="$HOME/.local/bin:$PATH"
export PATH

if [ -z "$SOURCE" ]; then
  command -v git >/dev/null 2>&1 || fail "git is missing. Run: xcode-select --install"
  SOURCE=$(mktemp -d)/thumbly
  say "Downloading Thumbly"
  git clone --quiet --depth 1 "$REPO" "$SOURCE"
fi
[ -f "$SOURCE/apps/device/pyproject.toml" ] || fail "$SOURCE is not a Thumbly checkout."

say "Installing the Thumbly service"
uv tool install --force --python 3.12 "$SOURCE/apps/device"

if [ -x "$SOURCE/apps/mac/scripts/bundle.sh" ]; then
  if xcrun --find swift >/dev/null 2>&1; then
    say "Building the Thumbly app"
    (cd "$SOURCE/apps/mac" && ./scripts/bundle.sh --install)
  else
    echo "Skipping the Mac app: Command Line Tools are missing. Run: xcode-select --install, then run this installer again."
  fi
fi

say "Registering the Thumbly service (it runs while the app is open)"
thumbly agent install

if [ -d "$APP" ]; then
  open "$APP"
else
  thumbly open
fi

cat <<'EOF'

Thumbly is installed.
  1. Plug your iPhone into this Mac, unlock it and tap Trust.
  2. Connect Claude Code:  claude mcp add --transport http thumbly http://127.0.0.1:8771/mcp
  3. Approve the request in the Thumbly app.
EOF
