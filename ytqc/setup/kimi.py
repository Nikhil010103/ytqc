"""kimi-webbridge automator: install the daemon → start it → report status.

The daemon ships a scriptable binary at ~/.kimi-webbridge/bin/kimi-webbridge with
`status` (JSON: running/extension_connected/extension_id/version), `start`, and
`install`. The Chrome extension itself is force-installed by setup/chrome.py; here
we own the daemon and read the extension's connection state from `status`."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from ytqc.setup.platform import (KIMI_EXTENSION_ID, Status, StepResult, is_windows,
                                 os_name, run, which)

BIN = Path.home() / ".kimi-webbridge" / "bin" / "kimi-webbridge"
INSTALL_SH = "https://kimi-web-img.moonshot.cn/webbridge/install.sh"
EXTENSION_PAGE = "https://www.kimi.com/features/webbridge"


def _binary() -> Optional[str]:
    if BIN.exists():
        return str(BIN)
    return which("kimi-webbridge")


def installed() -> bool:
    return _binary() is not None


def status() -> dict:
    """Parsed `kimi-webbridge status` JSON, or {} if unavailable."""
    b = _binary()
    if not b:
        return {}
    try:
        r = run([b, "status"], timeout=5)
        return json.loads((r.stdout or "").strip() or "{}")
    except Exception:
        return {}


def extension_id() -> str:
    """The connected extension's id (preferred) or the known store id fallback."""
    return status().get("extension_id") or KIMI_EXTENSION_ID


def install(console) -> StepResult:
    if installed():
        return StepResult("kimi daemon install", Status.OK, "already installed")
    if is_windows():
        # The curl|bash installer is macOS/Linux only; Windows is guided.
        return StepResult(
            "kimi daemon install", Status.ACTION, "manual install needed (Windows)",
            hint=f"install kimi-webbridge from {EXTENSION_PAGE}, then re-run `ytqc setup`")
    console.print("[dim]installing kimi-webbridge daemon (progress below)…[/]")
    try:
        # `set -o pipefail` so a curl failure (404/network) propagates as the exit
        # code instead of being masked by bash's success. capture=False → live
        # progress (so a slow download never looks frozen).
        r = run(["bash", "-c", f"set -o pipefail; curl -fsSL {INSTALL_SH} | bash"],
                timeout=600, capture=False)
    except Exception as exc:
        return StepResult("kimi daemon install", Status.FAIL, f"install failed — {exc}",
                          hint=f"install manually from {EXTENSION_PAGE}")
    if installed():
        return StepResult("kimi daemon install", Status.OK, "installed")
    if getattr(r, "returncode", 1) != 0:
        return StepResult("kimi daemon install", Status.FAIL,
                          "install failed (network or installer error)",
                          hint=f"check your connection, or install manually from {EXTENSION_PAGE}")
    return StepResult("kimi daemon install", Status.ACTION, "install did not complete",
                      hint=f"finish install from {EXTENSION_PAGE}, then re-run setup")


def ensure_running(console) -> StepResult:
    b = _binary()
    if not b:
        return StepResult("kimi daemon", Status.FAIL, "not installed")
    if status().get("running"):
        return StepResult("kimi daemon", Status.OK, "running")
    console.print("[dim]starting kimi-webbridge daemon…[/]")
    try:
        run([b, "start"], timeout=30)
    except Exception as exc:
        return StepResult("kimi daemon", Status.FAIL, f"could not start — {exc}")
    for _ in range(10):
        if status().get("running"):
            return StepResult("kimi daemon", Status.OK, "started")
        time.sleep(0.5)
    return StepResult("kimi daemon", Status.FAIL, "started but not reporting running",
                      hint=f"run `{b} status` to inspect")


def extension_state(console) -> StepResult:
    """Whether the Chrome extension is attached to the daemon. The extension is
    force-installed by chrome.py; here we just report the connection (which also
    needs Chrome open + the YouTube profile signed in)."""
    st = status()
    if not st:
        return StepResult("kimi extension", Status.FAIL, "daemon not responding")
    if st.get("extension_connected"):
        return StepResult("kimi extension", Status.OK,
                          f"connected (v{st.get('extension_version', '?')})")
    return StepResult(
        "kimi extension", Status.ACTION, "not connected yet",
        hint="open Chrome (with the kimi-webbridge extension, force-installed by setup) and "
             "sign into YouTube; the extension attaches when the window is focused.")


def ensure(console) -> list[StepResult]:
    results = [install(console)]
    if results[-1].blocking or results[-1].status == Status.ACTION:
        return results
    results.append(ensure_running(console))
    if results[-1].blocking:
        return results
    results.append(extension_state(console))
    return results
