"""Force-install the kimi-webbridge + VidIQ Chrome extensions via a managed
policy, so they auto-appear on the user's next Chrome launch — no Web Store
clicking. Uses the USER-scope policy on each OS (no admin):

  macOS:   `defaults write com.google.Chrome ExtensionInstallForcelist <array>`
  Windows: HKCU\\Software\\Policies\\Google\\Chrome\\ExtensionInstallForcelist
  Linux:   /etc/opt/chrome/policies/managed/*.json (needs root → guided)

Both extension IDs are Chrome-Web-Store hosted, so the standard store update URL
applies. kimi's id is read live from the daemon; VidIQ's is the known store id."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from ytqc.setup import kimi
from ytqc.setup.platform import (VIDIQ_EXTENSION_ID, WEBSTORE_UPDATE_URL, Status,
                                 StepResult, chrome_binary, is_macos, is_windows,
                                 os_name, run, spawn)

_ID_RE = re.compile(r"([a-p]{32});https?://[^\s\"']+")


def _entries() -> list[str]:
    """`<id>;<update_url>` for each extension we force-install."""
    ids = [kimi.extension_id(), VIDIQ_EXTENSION_ID]
    return [f"{eid};{WEBSTORE_UPDATE_URL}" for eid in dict.fromkeys(ids)]  # dedupe, keep order


# ── macOS (user defaults domain — read by Chrome as policy, no admin) ──────────
def _apply_macos() -> StepResult:
    wanted = _entries()
    existing: list[str] = []
    try:
        r = run(["defaults", "read", "com.google.Chrome", "ExtensionInstallForcelist"], timeout=15)
        if r.returncode == 0:
            existing = [m.group(0) for m in _ID_RE.finditer(r.stdout or "")]
    except Exception:
        pass
    merged = list(dict.fromkeys(existing + wanted))   # union, our entries guaranteed present
    try:
        run(["defaults", "write", "com.google.Chrome", "ExtensionInstallForcelist",
             "-array", *merged], timeout=15)
    except Exception as exc:
        return StepResult("chrome extensions", Status.FAIL, f"could not write policy — {exc}")
    return StepResult("chrome extensions", Status.OK,
                      f"force-install policy set ({len(wanted)} extensions)")


# ── Windows (HKCU policy — read by Chrome, no admin) ──────────────────────────
def _apply_windows() -> StepResult:
    key = r"HKCU\Software\Policies\Google\Chrome\ExtensionInstallForcelist"
    entries = _entries()
    try:
        for i, entry in enumerate(entries, start=1):
            r = run(["reg", "add", key, "/v", str(i), "/t", "REG_SZ", "/d", entry, "/f"], timeout=15)
            if r.returncode != 0:
                return StepResult("chrome extensions", Status.FAIL,
                                  f"registry write failed — {(r.stderr or '').strip()}")
    except Exception as exc:
        return StepResult("chrome extensions", Status.FAIL, f"could not write policy — {exc}")
    return StepResult("chrome extensions", Status.OK,
                      f"force-install policy set ({len(entries)} extensions)")


def apply_policy(console) -> StepResult:
    """Write the force-install policy for the current OS."""
    if is_macos():
        return _apply_macos()
    if is_windows():
        return _apply_windows()
    # Linux: managed-policy JSON needs root → guide.
    return StepResult(
        "chrome extensions", Status.ACTION, "manual policy needed (Linux)",
        hint="add a managed-policy JSON with ExtensionInstallForcelist under "
             "/etc/opt/chrome/policies/managed/, or install the extensions from the Web Store.")


def restart_chrome(console) -> StepResult:
    """Relaunch Chrome so it re-reads policy and pulls the forced extensions.
    Best-effort: if Chrome is open we ask the user to restart it (we don't kill it
    to avoid losing their tabs)."""
    chrome = chrome_binary()
    if not chrome:
        return StepResult("chrome restart", Status.ACTION, "Chrome not found",
                          hint="install Google Chrome, then re-run `ytqc setup`")
    return StepResult(
        "chrome restart", Status.ACTION, "restart Chrome to load the extensions",
        hint="fully quit and reopen Chrome (Cmd/Ctrl+Q), then sign into YouTube; "
             "the forced extensions install automatically on launch.")


def launch_chrome(console) -> Optional[int]:
    """Open Chrome (used by `ytqc start`)."""
    chrome = chrome_binary()
    if not chrome:
        return None
    return spawn([chrome])


def ensure(console) -> list[StepResult]:
    res = [apply_policy(console)]
    if not res[-1].blocking:
        res.append(restart_chrome(console))
    return res
