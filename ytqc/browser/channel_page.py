"""Channel extraction: /about via aboutChannelViewModel deep-find,
/videos grid via lockupViewModel (2026) with videoRenderer legacy fallback —
both probe-validated. Velocity math lifted from yt_qc_checker.py."""
from __future__ import annotations

import logging
import time

from ytqc.browser import youtube_js as J
from ytqc.browser.vidiq import scrape_vidiq
from ytqc.browser.webbridge import KimiClient
from ytqc.models import ChannelExtract, ChannelVideoTile
from ytqc.utils.parsing import parse_count, parse_date_to_days

log = logging.getLogger("ytqc.channel")


def _base_url(channel_id: str) -> str:
    if channel_id.startswith("UC") and len(channel_id) == 24:
        return f"https://www.youtube.com/channel/{channel_id}"
    handle = channel_id if channel_id.startswith("@") else f"@{channel_id}"
    return f"https://www.youtube.com/{handle}"


def extract_channel(
    kimi: KimiClient,
    channel_id: str,
    with_vidiq: bool = False,
    vidiq_timeout_s: float = 8.0,
    channel_pages: int = 4,
    channel_grid_shots: int = 4,
) -> ChannelExtract:
    ex = ChannelExtract(channel_id=channel_id)
    base = _base_url(channel_id)

    # ── /about ──────────────────────────────────────────────────────────
    kimi.navigate(f"{base}/about", ready_js=J.CHANNEL_READY)
    about = kimi.js(J.CHANNEL_ABOUT)
    if isinstance(about, dict) and about.get("ok"):
        ex.title = about.get("title", "")
        ex.external_id = about.get("externalId", "")
        ex.description = about.get("description", "")
        ex.subscribers = parse_count(about.get("subscriberCountText", ""))
        ex.total_views = parse_count(about.get("viewCountText", ""))
        ex.video_count = parse_count(about.get("videoCountText", ""))
        ex.country = about.get("country", "")
        ex.joined_date = about.get("joinedDateText", "").replace("Joined ", "")
        ex.links = about.get("links") or []
        ex.channel_keywords = about.get("channelKeywords", "")
        ex.is_family_safe = about.get("isFamilySafe")
        ex.provenance["about"] = "ytInitialData"
    else:
        ex.provenance["about"] = "none"
        log.warning("aboutChannelViewModel not found for %s", channel_id)

    try:
        ex.home_screenshot_b64 = kimi.screenshot_b64()
    except Exception:
        pass

    # Tab inventory (free — same ytInitialData): tells us whether a Shorts tab
    # exists, which the Shorts-only decision below needs.
    tabs = kimi.js(J.CHANNEL_TABS)
    if isinstance(tabs, dict) and tabs.get("tabs"):
        ex.tabs = [str(t) for t in tabs["tabs"]]
        ex.has_shorts_tab = any(t.strip().lower() == "shorts" for t in ex.tabs)

    # ── /videos ─────────────────────────────────────────────────────────
    kimi.navigate(f"{base}/videos", ready_js=J.CHANNEL_READY)
    # Lane tabs run backgrounded → YouTube only renders ~3 grid tiles and won't
    # infinite-scroll. Focus emulation makes the tab "visible" so the grid fills
    # in (~30 tiles) for screenshots. Best-effort: a bridge without CDP just
    # falls back to the hidden-tab behaviour.
    try:
        kimi.cdp("Emulation.setFocusEmulationEnabled", {"enabled": True})
    except Exception as exc:
        log.debug("focus emulation unavailable (%s) — grid screenshots may be sparse", exc)

    # Titles: full catalog via the continuation data API (visibility-independent).
    source = "continuation_api"
    grid = kimi.js(J.CHANNEL_VIDEOS_ALL.replace("__PAGES__", str(max(0, channel_pages))))
    if not (isinstance(grid, dict) and grid.get("n", 0) > 0):
        grid = kimi.js(J.CHANNEL_VIDEOS)            # sync fallback (first ~30)
        source = "lockupViewModel"
    if isinstance(grid, dict) and grid.get("n", 0) > 0:
        for v in grid["vids"]:
            ex.recent_videos.append(ChannelVideoTile(
                video_id=v["id"],
                title=v.get("title", ""),
                views=parse_count(v.get("views", "")),
                days_ago=parse_date_to_days(v.get("age", "")),
            ))
        ex.provenance["videos_grid"] = f"{source}:{len(ex.recent_videos)}"
    else:
        ex.provenance["videos_grid"] = "none"
        log.warning("no video grid extracted for %s", channel_id)
    ex.long_form_count = len(ex.recent_videos)

    # ── /shorts (only when there is no long-form catalog) ───────────────
    # Shorts never appear in the /videos grid. A channel with a Shorts tab and
    # an empty /videos grid is Shorts-only: visit /shorts so classification has
    # titles + thumbnails to work with (and so the QC "Shorts" column is real).
    if ex.long_form_count == 0 and ex.has_shorts_tab:
        _scrape_shorts_tab(kimi, ex, base)

    # Thumbnails: scroll through the rendered grid and screenshot each viewport
    # (of whichever catalog page we ended on — /videos, or /shorts above).
    ex.grid_screenshots_b64 = _capture_grid_shots(kimi, channel_grid_shots)
    ex.provenance["grid_shots"] = str(len(ex.grid_screenshots_b64))

    # VidIQ overlay (optional, failure-isolated) — the "Quick channel stats" block
    # is injected into #page-header and is present on every channel tab.
    if with_vidiq:
        ex.vidiq = scrape_vidiq(kimi, "channel", timeout_s=vidiq_timeout_s)
        ex.provenance["vidiq"] = "panel" if ex.vidiq.ok else "none"

    # velocity (lifted formula): avg of last 5 vs previous 5 uploads
    recent = [v.views for v in ex.recent_videos[:5]]
    prev = [v.views for v in ex.recent_videos[5:10]]
    avg_r = sum(recent) / len(recent) if recent else 0
    avg_p = sum(prev) / len(prev) if prev else 0
    ex.avg_views_last5 = round(avg_r, 0)
    ex.avg_views_prev5 = round(avg_p, 0)
    ex.velocity_score = round((avg_r - avg_p) / max(avg_p, 1), 4)

    if not ex.title and not ex.recent_videos:
        ex.ok = False
        ex.error = "channel page extraction failed (not found / renamed?)"
    return ex


def _scrape_shorts_tab(kimi: KimiClient, ex: ChannelExtract, base: str) -> None:
    """Read the /shorts catalog into `ex` and set the Shorts-only flags.

    Called only when /videos came back empty, so any Shorts found here mean the
    channel publishes Shorts exclusively. Best-effort: a failure leaves the
    channel looking like an empty catalog, exactly as before this existed."""
    try:
        kimi.navigate(f"{base}/shorts", ready_js=J.CHANNEL_READY)
        try:
            kimi.cdp("Emulation.setFocusEmulationEnabled", {"enabled": True})
        except Exception as exc:
            log.debug("focus emulation unavailable on /shorts (%s)", exc)
        shorts = kimi.js(J.CHANNEL_SHORTS)
    except Exception as exc:
        log.warning("shorts tab scrape failed for %s: %s", ex.channel_id, exc)
        return
    if not (isinstance(shorts, dict) and shorts.get("n", 0) > 0):
        ex.provenance["shorts_grid"] = "none"
        return
    for v in shorts["vids"]:
        ex.recent_videos.append(ChannelVideoTile(
            video_id=v["id"],
            title=v.get("title", ""),
            views=parse_count(v.get("views", "")),
            is_short=True,
        ))
    ex.shorts_count = len(ex.recent_videos)
    ex.is_shorts_only = True
    ex.provenance["shorts_grid"] = f"shortsLockupViewModel:{ex.shorts_count}"


def _capture_grid_shots(kimi: KimiClient, n: int) -> list[str]:
    """Scroll through the rendered /videos grid in even steps and screenshot each
    viewport — the shots show the video thumbnails for vision-based brand safety +
    categorization. Best-effort: failures are swallowed (channel QC still runs on
    titles + about). Returns the captured base64 JPEGs (deduped, blanks dropped)."""
    shots: list[str] = []
    if n <= 0:
        return shots
    try:
        for i in range(n):
            frac = i / max(n - 1, 1)        # 0.0 .. 1.0 across the page
            kimi.js(f"window.scrollTo(0, Math.round({frac} * "
                    "(document.documentElement.scrollHeight - window.innerHeight)));1")
            time.sleep(0.8)                 # let the grid settle after the jump
            shot = kimi.screenshot_b64()
            if shot and shot not in shots and len(shot) > 5000:
                shots.append(shot)
        kimi.js("window.scrollTo(0, 0)")
    except Exception as exc:
        log.warning("grid screenshot capture failed (harmless): %s", exc)
    return shots
