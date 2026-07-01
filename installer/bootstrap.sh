#!/usr/bin/env bash
# ytqc — zero-to-running bootstrap (macOS).
#
# For a machine with NOTHING installed. Save it and run, or pipe it from a host:
#   curl -fsSL "<URL>" | bash
#
# Installs: Homebrew → git, Python, pipx, Google Chrome → ytqc (from the public
# GitHub repo, no token or account needed) → then runs `ytqc setup` (Ollama +
# model, kimi-webbridge daemon, Chrome extensions). Everything except the three
# by-hand steps the wizard guides you through.
set -euo pipefail

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

# 3. Install ytqc from the public GitHub repo (no token, no account needed).
say "installing ytqc…"
pipx install --force "git+https://github.com/Nikhil010103/ytqc.git"

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
