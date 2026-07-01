"""Cross-platform primitives for the setup wizard — OS detection, command
discovery, subprocess running, port probing, and locating Chrome. Kept dependency
free (stdlib only) so it works before anything else is installed."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ── step result (shared by every wizard module) ───────────────────────────────
class Status:
    OK = "ok"          # already satisfied / just satisfied
    ACTION = "action"  # needs a manual user action we can't automate
    WARN = "warn"      # non-fatal (optional dependency missing)
    FAIL = "fail"      # broken; the wizard could not fix it


@dataclass
class StepResult:
    name: str
    status: str = Status.OK
    message: str = ""
    hint: str = ""

    @property
    def ok(self) -> bool:
        return self.status == Status.OK

    @property
    def blocking(self) -> bool:
        return self.status == Status.FAIL


# ── OS ────────────────────────────────────────────────────────────────────────
def os_name() -> str:
    """'macos' | 'windows' | 'linux'."""
    p = sys.platform
    if p.startswith("darwin"):
        return "macos"
    if p.startswith("win"):
        return "windows"
    return "linux"


def is_windows() -> bool:
    return os_name() == "windows"


def is_macos() -> bool:
    return os_name() == "macos"


# ── commands / processes ──────────────────────────────────────────────────────
def which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def run(cmd: list[str], timeout: float = 120, env: Optional[dict] = None,
        capture: bool = True) -> subprocess.CompletedProcess:
    """Run a command. Never raises on non-zero exit (check the returncode);
    raises only on timeout/launch failure, which callers handle."""
    return subprocess.run(
        cmd, timeout=timeout, text=True,
        capture_output=capture,
        env={**os.environ, **(env or {})} if env else None,
    )


def spawn(cmd: list[str], env: Optional[dict] = None) -> Optional[int]:
    """Start a detached background process (e.g. `ollama serve`). Returns the pid
    or None on failure. Output is discarded — these are long-lived daemons."""
    try:
        kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if is_windows():
            kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP
        else:
            kwargs["start_new_session"] = True
        p = subprocess.Popen(cmd, env={**os.environ, **(env or {})} if env else None, **kwargs)
        return p.pid
    except Exception:
        return None


def port_open(host: str = "127.0.0.1", port: int = 11434, timeout: float = 1.0) -> bool:
    """True if something is listening — a cheap 'is the daemon up?' probe."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ── Chrome ────────────────────────────────────────────────────────────────────
def chrome_binary() -> Optional[str]:
    """Best-effort path to the Chrome executable for the current OS."""
    name = os_name()
    if name == "macos":
        p = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        return p if Path(p).exists() else None
    if name == "windows":
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                p = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
                if p.exists():
                    return str(p)
        return which("chrome")
    return which("google-chrome") or which("chromium") or which("chromium-browser")


# Chrome Web Store IDs (confirmed live). kimi is also read dynamically from the
# daemon's status.extension_id; this is the fallback.
KIMI_EXTENSION_ID = "fldmhceldgbpfpkbgopacenieobmligc"
VIDIQ_EXTENSION_ID = "pachckjkecffpdphbpmfolblodfkgbhl"
ADBLOCK_YT_EXTENSION_ID = "cmedhionkhpnakcndndgjdbohmhepckk"  # "Adblock for Youtube™" — cuts ad-wait during QC
WEBSTORE_UPDATE_URL = "https://clients2.google.com/service/update2/crx"
