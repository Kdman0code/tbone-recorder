#!/usr/bin/env bash
# Installer for tbone-recorder on macOS and Linux.
#   curl -fsSL https://raw.githubusercontent.com/Kdman0code/tbone-recorder/main/install.sh | bash
set -euo pipefail

REPO="${TBONE_REPO:-https://github.com/Kdman0code/tbone-recorder}"
BRANCH="${TBONE_BRANCH:-main}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$1"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$1" >&2; }
die() { printf '\033[1;31mxx\033[0m %s\n' "$1" >&2; exit 1; }

say "Installing tbone-recorder"

if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv (fetches its own Python, so you do not need one)"
  curl -LsSf https://astral.sh/uv/install.sh | sh || die "Could not install uv."
  # The installer drops uv in one of these; pick it up for this shell.
  for candidate in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
    [ -x "$candidate/uv" ] && export PATH="$candidate:$PATH"
  done
fi

command -v uv >/dev/null 2>&1 || die "uv is installed but not on PATH. Open a new terminal and re-run."

say "Installing the app"
if command -v git >/dev/null 2>&1; then
  uv tool install --force "git+${REPO}@${BRANCH}"
else
  # No git: install straight from the source archive instead.
  uv tool install --force "${REPO}/archive/refs/heads/${BRANCH}.zip"
fi

uv tool update-shell >/dev/null 2>&1 || true

echo
say "Done. Start it with:"
echo "    tbone-rec"
echo
if ! command -v tbone-rec >/dev/null 2>&1; then
  warn "tbone-rec is not on this shell's PATH yet - open a new terminal first."
  warn "(uv installs tools into ~/.local/bin)"
fi
if [ "$(uname -s)" = "Darwin" ]; then
  echo "macOS: the first run asks for Microphone permission for your terminal."
  echo "If the meter stays flat, enable it under"
  echo "System Settings > Privacy & Security > Microphone."
fi
