"""Frame + thumbnail capture. Canvas grab is primary (probe-validated:
MSE-sourced video doesn't taint canvas, ~0.3s/frame). Fallback: full-page
screenshot when canvas fails (DRM/SecurityError/black frames)."""
from __future__ import annotations

import base64
import io
import logging
import time

import httpx

from ytqc.browser import youtube_js as J
from ytqc.browser.webbridge import KimiClient
from ytqc.models import FrameSet

log = logging.getLogger("ytqc.frames")

try:
    from PIL import Image
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

# largest accepted decoded image size (bytes) — guards against absurd frames
_MAX_IMG_BYTES = 5_000_000
# base64 inflates ~4/3; a b64 string longer than this implies > ~5MB decoded
_MAX_B64_LEN = (_MAX_IMG_BYTES * 4) // 3

_pil_warned = False


def fetch_thumbnail_b64(video_id: str) -> str | None:
    """i.ytimg.com is a public CDN — plain httpx, no browser roundtrip."""
    for name in ("maxresdefault", "hqdefault", "mqdefault"):
        try:
            r = httpx.get(f"https://i.ytimg.com/vi/{video_id}/{name}.jpg", timeout=10)
            # 404 placeholder ~1KB; reject oversized payloads (> 5MB) — try next size
            if (r.status_code == 200 and 2_000 < len(r.content) <= _MAX_IMG_BYTES):
                return base64.b64encode(r.content).decode()
        except Exception:
            continue
    return None


def _is_blank_frame(b64: str) -> bool:
    """Cheap luminance/size check — reject black (DRM), white/near-blank, or
    suspiciously tiny frames that carry no usable content."""
    global _pil_warned
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return False
    if len(raw) < 5_000:          # suspiciously tiny decoded payload
        return True
    if not _PIL_OK:
        if not _pil_warned:
            log.warning("PIL not available — blank-frame detection disabled")
            _pil_warned = True
        return False
    try:
        img = Image.open(io.BytesIO(raw)).convert("L")
        img = img.resize((32, 18))
        pixels = list(img.getdata())
        mean = sum(pixels) / len(pixels)
        return mean < 8 or mean > 248
    except Exception:
        return False


def _ad_gate(kimi: KimiClient, max_wait: float = 12.0, poll: float = 0.5) -> None:
    """Clear any pre-roll before seeking content frames. AD_SKIP asserts muted
    playback, detects the ad via class OR getAdState, and persistently clicks the
    skip control the instant it becomes clickable (~5s in). Returns as soon as the
    ad clears; caps at max_wait for non-skippable bumpers."""
    deadline = time.time() + max_wait
    saw_ad = False
    while time.time() < deadline:
        out = kimi.js(J.AD_SKIP)
        if not (isinstance(out, dict) and out.get("ad")):
            return                       # no ad, or it just cleared
        saw_ad = True
        time.sleep(poll)                 # fast poll re-clicks skip each cycle
    if saw_ad:
        log.warning("ad still showing after %.0fs — non-skippable bumper or stale "
                    "skip selector; frames may include ad content", max_wait)


def capture_frames(
    kimi: KimiClient,
    video_id: str,
    timestamps: list[float],
    is_live: bool = False,
) -> FrameSet:
    fs = FrameSet(thumbnail_b64=fetch_thumbnail_b64(video_id))
    if is_live:
        timestamps = []          # never seek a live stream; grab live head once
    # clear any pre-roll before seeking (AD_SKIP itself asserts muted playback)
    cfg = getattr(kimi, "cfg", None)
    ad_max = getattr(cfg, "ad_max_wait_s", 12.0)
    ad_poll = getattr(cfg, "ad_poll_s", 0.5)
    _ad_gate(kimi, max_wait=ad_max, poll=ad_poll)
    kimi.js(J.PLAYER_QUALITY)

    grabbed: list[str] = []
    used_ts: list[float] = []
    canvas_dead = False

    targets = timestamps or ([0.0] if is_live else [])
    for t in targets:
        try:
            if not is_live:
                kimi.js(J.FRAME_SEEK.replace("__T__", f"{t:.1f}"))
                deadline = time.time() + 6.0
                while time.time() < deadline:
                    st = kimi.js(J.FRAME_READY)
                    if isinstance(st, dict) and st.get("ad"):
                        # a mid-roll appeared — clear it before grabbing, else we'd
                        # capture ad frames; then re-seek the content timestamp
                        _ad_gate(kimi, max_wait=ad_max, poll=ad_poll)
                        kimi.js(J.FRAME_SEEK.replace("__T__", f"{t:.1f}"))
                    elif isinstance(st, dict) and not st.get("seeking") and st.get("ready"):
                        break
                    time.sleep(0.25)
            if not canvas_dead:
                out = kimi.js(J.FRAME_GRAB, timeout=30)
                if isinstance(out, str) and out.startswith("data:image"):
                    b64 = out.split(",", 1)[1]
                    if len(b64) > _MAX_B64_LEN:
                        log.warning("canvas frame@%.0fs too large (%d b64 chars) — skipping",
                                    t, len(b64))
                        continue
                    if not _is_blank_frame(b64):
                        grabbed.append(b64)
                        used_ts.append(t)
                        continue
                canvas_dead = True
                log.info("canvas grab failed (%s) — switching to screenshot fallback",
                         str(out)[:40])
            shot = kimi.screenshot_b64()
            if shot and len(shot) <= _MAX_B64_LEN and not _is_blank_frame(shot):
                grabbed.append(shot)
                used_ts.append(t)
        except Exception as exc:
            log.warning("frame@%.0fs failed: %s", t, exc)

    kimi.js(J.PLAYER_PAUSE)
    fs.frames_b64 = grabbed
    fs.frame_timestamps = used_ts
    fs.method = "none" if not grabbed else ("screenshot" if canvas_dead else "canvas")
    return fs
