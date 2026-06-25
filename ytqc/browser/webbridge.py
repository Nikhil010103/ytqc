"""kimi-webbridge client — lifted from yt_qc_checker.py and hardened:
jittered pacing, readiness polling instead of fixed sleeps, captcha detection."""
from __future__ import annotations

import json
import logging
import random
import time
from typing import Any

import httpx

from ytqc.config import BrowserConfig

log = logging.getLogger("ytqc.browser")


class CaptchaInterstitial(RuntimeError):
    """YouTube served a bot-check page — the run must halt, never retry into it."""


class BridgeNotConnected(RuntimeError):
    """The kimi-webbridge daemon is up but no Chrome extension/browser is
    connected to it (its MV3 service worker may have gone idle, or the Chrome
    window with the extension is closed/unfocused). A fatal setup problem — the
    run can't browse until a Chrome tab with the extension reconnects."""


class KimiClient:
    def __init__(self, cfg: BrowserConfig, rate_bucket=None, halt=None):
        self.cfg = cfg
        self._http = httpx.Client(timeout=45.0)
        self._items_done = 0
        # optional shared governors (multi-lane): a TokenBucket bounding the
        # aggregate navigation rate across all lanes, and a global halt Event.
        self._rate_bucket = rate_bucket
        self._halt = halt

    # ── raw bridge ────────────────────────────────────────────────────────
    def _kimi(self, action: str, args: dict, timeout: float = 45.0) -> dict:
        payload = {"action": action, "args": args, "session": self.cfg.session}
        attempts = 3
        for attempt in range(attempts):
            try:
                r = self._http.post(self.cfg.kimi_url, json=payload, timeout=timeout)
                r.raise_for_status()
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                # true transport timeout / connection error — retry with short backoff
                if attempt + 1 >= attempts:
                    raise
                backoff = 2.0 * (attempt + 1)
                log.warning("kimi-webbridge transport error (attempt %d/%d) — retry in %.0fs: %s",
                            attempt + 1, attempts, backoff, exc)
                if self._sleep_or_halt(backoff):
                    raise                 # halt requested during backoff — stop retrying
                continue
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                # The bridge returns 502 carrying an ok:false tool-error body —
                # that's a genuine tool error, not a transport fault: fast-fail.
                try:
                    body = exc.response.json()
                except Exception:
                    body = None
                if isinstance(body, dict) and body.get("ok") is False:
                    msg = str(body.get("error", body))
                    if "no extension connected" in msg.lower():
                        raise BridgeNotConnected(msg)
                    raise RuntimeError(f"kimi-webbridge error: {msg}")
                # 4xx (auth / not-found) — permanent, fast-fail.
                if 400 <= code < 500 and code != 429:
                    raise
                if attempt + 1 >= attempts:
                    raise
                # 429 → longer backoff; 5xx-without-tool-body → standard backoff.
                backoff = (8.0 * (attempt + 1)) if code == 429 else (2.0 * (attempt + 1))
                log.warning("kimi-webbridge HTTP %d (attempt %d/%d) — retry in %.0fs",
                            code, attempt + 1, attempts, backoff)
                if self._sleep_or_halt(backoff):
                    raise                 # halt requested during backoff — stop retrying
                continue
            resp = r.json()
            if not resp.get("ok"):
                raise RuntimeError(f"kimi-webbridge error: {resp.get('error', resp)}")
            return resp.get("data", {})
        # exhausted retries without returning or raising inside the loop
        raise RuntimeError("kimi-webbridge: retries exhausted")

    def _sleep_or_halt(self, seconds: float) -> bool:
        """Back off for `seconds`, but wake immediately if a halt is requested
        (Ctrl-C during a run). Returns True if halt fired — the caller should
        stop retrying so the lane thread unwinds promptly instead of sleeping
        out the full backoff and logging after the run was cancelled."""
        if self._halt is not None:
            return self._halt.wait(seconds)   # True if set within the window
        time.sleep(seconds)
        return False

    def js(self, code: str, timeout: float = 45.0) -> Any:
        """Run JavaScript and return the parsed result."""
        result = self._kimi("evaluate", {"code": code}, timeout)
        val = result.get("value", "")
        if isinstance(val, str):
            try:
                return json.loads(val)
            except json.JSONDecodeError:
                return val
        return val

    def screenshot_b64(self) -> str:
        data = self._kimi("screenshot", {"format": "jpeg", "quality": 70}, timeout=60)
        return data.get("data", "")

    def cdp(self, method: str, params: dict | None = None) -> dict:
        """Invoke a Chrome DevTools Protocol method via the bridge. Used e.g. to
        enable focus emulation so a backgrounded lane tab still renders/loads
        content (YouTube's grid only fills in when the tab is 'visible')."""
        return self._kimi("cdp", {"method": method, "params": params or {}})

    def scroll(self, px: int = 500, settle: float = 0.8) -> None:
        self._kimi("evaluate", {"code": f"window.scrollBy(0,{px})"})
        time.sleep(settle)

    def close(self) -> None:
        try:
            self._kimi("close_session", {})
        except Exception:
            pass

    # ── navigation with readiness polling ─────────────────────────────────
    def navigate(self, url: str, new_tab: bool = False, ready_js: str | None = None) -> None:
        # aggregate request-rate ceiling across all lanes (bot hygiene)
        if self._rate_bucket is not None:
            self._rate_bucket.acquire(stop=self._halt)
        self._kimi("navigate", {"url": url, "newTab": new_tab})
        time.sleep(random.uniform(self.cfg.nav_sleep_min, self.cfg.nav_sleep_max) * 0.5)
        self._wait_ready(ready_js)
        self._consent_gate()
        self._captcha_gate()

    def _wait_ready(self, ready_js: str | None) -> None:
        """Poll for page-specific readiness instead of a long fixed sleep."""
        check = ready_js or "JSON.stringify({r: document.readyState === 'complete'})"
        deadline = time.time() + self.cfg.ready_timeout_s
        while time.time() < deadline:
            try:
                out = self.js(check)
                if isinstance(out, dict) and out.get("r"):
                    return
            except Exception:
                pass
            time.sleep(0.4)
        log.debug("readiness poll timed out — continuing anyway")

    def _consent_gate(self) -> None:
        try:
            self.js(
                """JSON.stringify((() => {
                  const b = document.querySelector(
                    'ytd-consent-bump-v2-lightbox button[aria-label*="Accept" i], '
                    + 'tp-yt-paper-dialog button[aria-label*="Accept" i]');
                  if (b) { b.click(); return {clicked: true}; }
                  return {clicked: false};
                })())"""
            )
        except Exception:
            pass

    def _captcha_gate(self) -> None:
        try:
            out = self.js(
                """JSON.stringify({sorry: document.title.includes('Sorry'),
                                   captcha: !!document.querySelector('iframe[src*="recaptcha"]')})"""
            )
            if isinstance(out, dict) and (out.get("sorry") or out.get("captcha")):
                raise CaptchaInterstitial("YouTube bot-check interstitial detected")
        except CaptchaInterstitial:
            raise
        except Exception:
            pass

    # ── pacing ────────────────────────────────────────────────────────────
    def item_pause(self) -> None:
        """Jittered pause between items + periodic 'coffee break'. The pause is
        halt-aware so Ctrl-C tears the lane down promptly instead of sleeping
        out a long coffee break."""
        self._items_done += 1
        if self.cfg.coffee_every and self._items_done % self.cfg.coffee_every == 0:
            pause = random.uniform(self.cfg.coffee_min, self.cfg.coffee_max)
            log.info("coffee pause %.0fs after %d items", pause, self._items_done)
            self._sleep_or_halt(pause)
        else:
            self._sleep_or_halt(random.uniform(self.cfg.item_sleep_min, self.cfg.item_sleep_max))

    # ── store-then-poll helper for async in-page work ─────────────────────
    def poll_window_var(self, var: str, timeout_s: float = 10.0, interval: float = 0.3) -> Any:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            out = self.js(f"JSON.stringify(window.{var} || {{state:'missing'}})")
            if isinstance(out, dict) and out.get("state") not in ("pending", "missing"):
                return out
            time.sleep(interval)
        return {"state": "timeout"}
