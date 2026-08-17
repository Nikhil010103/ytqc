"""Keep the machine awake for the duration of a run.

A long QC run dies quietly if the Mac goes to sleep: the browser tabs are
suspended mid-navigation and the lanes time out. Locking the screen does NOT
kill anything by itself — sleep does. So while a run is in flight we hold a
power assertion via macOS `caffeinate`:

    caffeinate -dimsu -w <our pid>

  -d  display stays on          -i  no idle sleep
  -m  no disk sleep             -s  no system sleep (on AC)
  -u  declare "user is active"  -w  auto-release when our pid exits

`-d` matters most: Chrome only reliably paints (and `captureVisibleTab` only
reliably returns pixels) while the display is awake, and the pipeline reads
channel thumbnails/video frames from screenshots. Locking the screen while the
display stays on is fine; letting the display sleep degrades the vision pass.

No-op on non-macOS and whenever `caffeinate` is missing — never fatal.
"""
from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import sys

log = logging.getLogger("ytqc.keepawake")


def available() -> bool:
    return sys.platform == "darwin" and shutil.which("caffeinate") is not None


@contextlib.contextmanager
def keep_awake(enabled: bool = True):
    """Hold a no-sleep assertion for the with-block. Yields True if one is
    actually held, False if it couldn't be (or wasn't wanted)."""
    proc = None
    if enabled and available():
        try:
            proc = subprocess.Popen(
                ["caffeinate", "-dimsu", "-w", str(os.getpid())],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError as exc:                  # pragma: no cover - defensive
            log.debug("caffeinate failed to start: %s", exc)
            proc = None
    try:
        yield proc is not None
    finally:
        if proc is not None:
            # -w already releases on our exit; terminate explicitly so the
            # assertion also drops the moment a long-lived chat session's run
            # finishes, rather than at process exit.
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:                   # pragma: no cover - defensive
                log.debug("caffeinate cleanup failed", exc_info=True)
