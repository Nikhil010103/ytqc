"""OS-level dependency bootstrap: Homebrew (macOS) and Google Chrome — the pieces a
truly clean machine lacks before the rest of the wizard can run. Every step is
idempotent: it checks for an existing install first and is a no-op when present."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ytqc.setup.platform import (Status, StepResult, chrome_binary, is_macos,
                                 os_name, run, which)

HOMEBREW_INSTALL_URL = "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
# brew isn't on PATH in the shell that just installed it; check both prefixes.
_BREW_PATHS = ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")  # Apple Silicon, Intel


def brew_path() -> Optional[str]:
    """Resolve the brew binary even when it isn't yet on PATH."""
    p = which("brew")
    if p:
        return p
    for cand in _BREW_PATHS:
        if Path(cand).exists():
            return cand
    return None


def ensure_homebrew(console) -> StepResult:
    """Install Homebrew on macOS if missing. No-op on other OSes or when present.
    Homebrew is the package manager the wizard uses to install Ollama + Chrome, so a
    clean Mac needs it first."""
    if not is_macos():
        return StepResult("homebrew", Status.OK, "not needed on this OS")
    if brew_path():
        return StepResult("homebrew", Status.OK, "already installed")
    console.print("[dim]installing Homebrew (you may be prompted for your macOS password)…[/]")
    try:
        # NONINTERACTIVE skips the RETURN confirmation; sudo may still prompt once.
        run(["bash", "-c",
             f'NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL {HOMEBREW_INSTALL_URL})"'],
            timeout=900, capture=False)
    except Exception as exc:
        return StepResult("homebrew", Status.FAIL, f"install failed — {exc}",
                          hint="install Homebrew from https://brew.sh, then re-run `ytqc setup`")
    if brew_path():
        return StepResult("homebrew", Status.OK, "installed")
    return StepResult("homebrew", Status.ACTION, "install did not complete",
                      hint="finish Homebrew from https://brew.sh, then re-run `ytqc setup`")


def chrome_installed() -> bool:
    return chrome_binary() is not None


def ensure_chrome(console) -> StepResult:
    """Install Google Chrome if it's missing. macOS → Homebrew cask; Windows → winget.
    Chrome is required: QC drives a real Chrome and the extensions live in it."""
    if chrome_installed():
        return StepResult("google chrome", Status.OK, "already installed")
    name = os_name()
    try:
        if name == "macos":
            brew = brew_path()
            if not brew:
                return StepResult("google chrome", Status.ACTION, "needs Homebrew first",
                                  hint="let the wizard install Homebrew (step 1), then re-run `ytqc setup`")
            console.print("[dim]installing Google Chrome via Homebrew…[/]")
            run([brew, "install", "--cask", "google-chrome"], timeout=600, capture=False)
        elif name == "windows" and which("winget"):
            console.print("[dim]installing Google Chrome via winget…[/]")
            run(["winget", "install", "-e", "--id", "Google.Chrome",
                 "--accept-package-agreements", "--accept-source-agreements"],
                timeout=600, capture=False)
        else:
            return StepResult("google chrome", Status.ACTION, "install Chrome manually",
                              hint="download Google Chrome from https://www.google.com/chrome/, then re-run setup")
    except Exception as exc:
        return StepResult("google chrome", Status.FAIL, f"install failed — {exc}",
                          hint="install Chrome from https://www.google.com/chrome/")
    if chrome_installed():
        return StepResult("google chrome", Status.OK, "installed")
    return StepResult("google chrome", Status.ACTION, "install did not complete",
                      hint="finish installing Chrome from https://www.google.com/chrome/, then re-run setup")
