"""VidIQ overlay scraping — best-effort read of the VidIQ browser-extension
panel from the page DOM (light DOM, no shadow root; validated live 2026).

Fully optional and failure-isolated: if the extension/panel is absent, slow, or
on a free plan with locked sections, this returns an ignorable VidIQStats
(ok=False) and NEVER raises — the pipeline runs exactly as before. The one
exception re-raised is CaptchaInterstitial, matching the rest of the browser
layer (a bot-check must always halt the lane, never be swallowed)."""
from __future__ import annotations

import logging
import time
from typing import Literal

from ytqc.browser import youtube_js as J
from ytqc.browser.webbridge import CaptchaInterstitial, KimiClient
from ytqc.models import VidIQStats

log = logging.getLogger("ytqc.vidiq")


def scrape_vidiq(
    kimi: KimiClient,
    scope: Literal["video", "channel"],
    timeout_s: float = 8.0,
) -> VidIQStats:
    """Poll for the VidIQ panel and scrape it. The panel renders asynchronously
    (the extension makes its own network calls after page load), so we poll the
    scrape probe — which self-reports present:false until populated — up to
    timeout_s before giving up."""
    out = VidIQStats(scope=scope)
    probe = J.VIDIQ_VIDEO_SCRAPE if scope == "video" else J.VIDIQ_CHANNEL_SCRAPE
    try:
        # The headline values (subs) render before late sections — video SEO score,
        # channel "similar channels" — so once the panel reports present, give the
        # late fields a few extra cycles to populate before accepting the read.
        data = None
        deadline = time.time() + timeout_s
        grace = 0
        while time.time() < deadline:
            r = kimi.js(probe)
            if isinstance(r, dict) and r.get("present"):
                data = r
                late_ok = bool(r.get("seo_score")) if scope == "video" \
                    else bool(r.get("similar_channels"))
                if late_ok or grace >= 3:
                    break
                grace += 1
                time.sleep(0.6)
                continue
            time.sleep(0.5)
        if data is None:
            out.error = "vidiq panel not detected (extension absent or slow)"
            return out

        out.present = True
        out.raw_text = data.get("raw_text", "")
        out.subscribers_text = data.get("subscribers", "")
        out.video_count_text = data.get("video_count", "")
        if scope == "video":
            out.total_views_text = data.get("total_views", "")
            out.channel_age_text = data.get("channel_age", "")
            out.seo_score_text = data.get("seo_score", "")
            out.controversial_locked = bool(data.get("controversial_locked"))
        else:
            out.subscribers_growth_text = data.get("subscribers_growth", "")
            out.views_gained_7d_text = data.get("views_gained_7d", "")
            out.rank_text = data.get("rank", "")
            out.est_monthly_earnings_text = data.get("est_monthly_earnings", "")
            out.avg_video_length_text = data.get("avg_video_length", "")
            out.upload_frequency_text = data.get("upload_frequency", "")
            out.similar_channels = [str(s) for s in (data.get("similar_channels") or [])]
        out.ok = True
    except CaptchaInterstitial:
        raise
    except Exception as exc:
        log.warning("vidiq scrape failed (harmless, continuing): %s", exc)
        out.error = str(exc)[:200]
    return out
