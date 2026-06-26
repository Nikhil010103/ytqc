#!/usr/bin/env bash
# ytqc — zero-to-running bootstrap (macOS).
#
# For a machine with NOTHING installed. Save it and run, or:
#   curl -fsSL "$BASE/bootstrap.sh" | bash
#
# Installs: Homebrew → git, Python, pipx, Google Chrome → ytqc → then runs `ytqc setup`
# (which installs Ollama + the model, the kimi-webbridge daemon, and the Chrome
# extensions). Everything except the three by-hand steps the wizard guides you through.
#
# Auth: pulls ytqc from the private Bitbucket repo over SSH, so add your Bitbucket SSH
# key first (Bitbucket → Personal settings → SSH keys). Override the repo with
# YTQC_REPO if needed.
set -euo pipefail

REPO="${YTQC_REPO:-git+ssh://git@bitbucket.org/silverpush/yt-qc-agent.git}"
say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "this bootstrap is for macOS; on Windows use bootstrap.ps1"

# 1. Homebrew
if ! command -v brew >/dev/null 2>&1; then
  say "installing Homebrew (you may be prompted for your macOS password)…"
  NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
fi
# Put brew on PATH for THIS shell (Apple Silicon vs Intel).
if [ -x /opt/homebrew/bin/brew ]; then eval "$(/opt/homebrew/bin/brew shellenv)"
elif [ -x /usr/local/bin/brew ]; then eval "$(/usr/local/bin/brew shellenv)"; fi
command -v brew >/dev/null 2>&1 || die "Homebrew install failed — see https://brew.sh"

# 2. Core tools + Chrome
say "installing git, Python, pipx…"
brew install git python pipx
say "installing Google Chrome…"
brew install --cask google-chrome || say "Chrome already present — skipping"
pipx ensurepath >/dev/null 2>&1 || true
export PATH="$HOME/.local/bin:$PATH"   # pipx tool bin for this shell

# 3. ytqc (from Bitbucket over SSH)
say "installing ytqc from Bitbucket (SSH)…"
pipx install --force "$REPO"

YTQC="$(command -v ytqc || echo "$HOME/.local/bin/ytqc")"
[ -x "$YTQC" ] || die "ytqc installed but not on PATH — open a new terminal and run: ytqc setup"
say "installed: $("$YTQC" version 2>/dev/null || echo ytqc)"

# 4. Setup (reattach a TTY when piped through curl|bash).
if [ -e /dev/tty ]; then
  say "starting ytqc setup…"
  exec "$YTQC" setup </dev/tty
else
  say "Install complete. Open a new terminal and run:  ytqc setup"
fi
