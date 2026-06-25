"""Run orchestration: N browser lanes (each its own tab/session) + M analysis
workers, decoupled by a bounded queue.

Each lane owns a KimiClient bound to a distinct kimi session ("ytqc-lane{i}"),
so the lanes' tabs run concurrently (validated: kimi-webbridge gives true
parallelism per session). Lanes pull from a shared work queue (work-stealing —
self-balances heavy channels vs light videos), extract, and hand a bundle to the
M analysis workers, which share one thread-safe LLMClient. Aggregate browser
request rate is bounded by a shared token bucket; a circuit breaker sheds lanes
on captcha/stress before a global halt; a semaphore caps concurrent LLM calls."""
from __future__ import annotations

import logging
import queue
import random
import threading
import time

import httpx
from dataclasses import dataclass, field

from rich.console import Console, Group
from rich.live import Live
from rich.progress import (BarColumn, Progress, TextColumn,
                           TimeElapsedColumn, TimeRemainingColumn)
from rich.text import Text

from ytqc.agent import ui
from ytqc.browser.channel_page import extract_channel
from ytqc.browser.extract_cache import ExtractCache
from ytqc.browser.video_page import extract_video
from ytqc.browser.webbridge import (BridgeNotConnected, CaptchaInterstitial,
                                    KimiClient)
from ytqc.config import YtqcConfig
from ytqc.llm.cache import ResponseCache
from ytqc.llm.client import LLMClient
from ytqc.models import ChannelExtract, InputItem, QCRecord, VideoExtract
from ytqc.pipeline.channel_flow import run_channel_flow
from ytqc.pipeline.governor import CircuitBreaker, TokenBucket
from ytqc.pipeline.state import RunState
from ytqc.pipeline.video_flow import run_video_flow
from ytqc.sinks.base import ResultSink

log = logging.getLogger("ytqc.orchestrator")
_SENTINEL = object()


@dataclass
class RunStats:
    done: int = 0
    extracted: int = 0
    errors: int = 0
    unsafe: int = 0
    needs_review: int = 0
    tier_counts: dict = field(default_factory=dict)


class Orchestrator:
    def __init__(
        self,
        cfg: YtqcConfig,
        items: list[InputItem],
        sinks: list[ResultSink],
        state: RunState,
        provider: str | None = None,
        model: str | None = None,
        use_cache: bool = True,
        use_extract_cache: bool = True,
        comments: bool = True,
        extract_only: bool = False,
        console: Console | None = None,
    ):
        self.cfg = cfg
        self.items = items
        self.sinks = sinks
        self.state = state
        self.extract_only = extract_only
        self.comments = comments
        self.console = console or Console()
        self.stats = RunStats()
        self._sink_lock = threading.Lock()
        self._count_lock = threading.Lock()     # guards stats.extracted (lane writers)
        self._browser_done = threading.Event()  # set when all lanes have exited (tabs closed)
        self._halt = threading.Event()
        self._halt_announced = False
        self._setup_error: str | None = None   # set if lanes can't reach the browser

        self.lanes = max(1, cfg.pipeline.browser_lanes)
        self.workers = max(1, cfg.pipeline.analysis_workers)
        # bundle queue sized so every lane can drop one freshly-extracted bundle
        # and every worker can hold one in flight without lanes blocking on put.
        self._queue: queue.Queue = queue.Queue(maxsize=self.workers + self.lanes)
        self._work_q: queue.Queue = queue.Queue()       # shared item queue (work-stealing)
        self._worker_threads: list[threading.Thread] = []

        # shared governors
        self._bucket = TokenBucket(cfg.pipeline.max_pages_per_min)
        self._breaker = CircuitBreaker(
            min_lanes=cfg.pipeline.min_lane_count, start_lanes=self.lanes,
            enabled=cfg.pipeline.degrade_on_captcha,
        )

        profile = cfg.provider(provider).model_copy()
        if model:
            profile.model = model
        cache = ResponseCache(ttl_days=cfg.pipeline.cache_ttl_days, enabled=use_cache)
        sema = threading.BoundedSemaphore(max(1, cfg.pipeline.llm_concurrency))
        self.llm = LLMClient(profile, provider or cfg.active_provider, cache=cache, semaphore=sema)

        # cross-run extraction cache (skips re-scraping a recently-seen id)
        self._extract_cache = ExtractCache(
            video_ttl_days=cfg.pipeline.extraction_ttl_days,
            channel_ttl_days=cfg.pipeline.extraction_ttl_days_channel,
            enabled=use_extract_cache and cfg.pipeline.extract_cache,
        )

    def _put_sentinel(self) -> None:
        """Queue one sentinel without deadlocking on a full queue when consumers
        have already died."""
        while True:
            try:
                self._queue.put(_SENTINEL, timeout=1)
                return
            except queue.Full:
                if not any(w.is_alive() for w in self._worker_threads):
                    return

    def _shutdown_lanes(self, n_lanes: int) -> None:
        """Close every lane's browser tab/session by name. Safe to call from the
        main thread on Ctrl-C: it issues independent HTTP close_session calls
        (NOT the lanes' own httpx clients, which would race), so the Chrome tabs
        are torn down even while lane threads are still blocked mid-request.
        Closing a not-yet-opened or already-closed session is harmless."""
        base = self.cfg.browser.session
        for i in range(n_lanes):
            try:
                httpx.post(self.cfg.browser.kimi_url,
                           json={"action": "close_session", "args": {},
                                 "session": f"{base}-lane{i}"},
                           timeout=8)
            except Exception:
                pass

    # ── browser lane: owns one tab/session ────────────────────────────────
    def _lane(self, lane_index: int) -> None:
        kimi = None
        try:
            # stagger startup so N lanes don't fire N simultaneous watch loads at t=0
            stagger = random.uniform(0, self.cfg.pipeline.lane_stagger_s)
            if stagger:
                if self._halt.wait(timeout=stagger):
                    return
            lane_cfg = self.cfg.browser.model_copy(
                update={"session": f"{self.cfg.browser.session}-lane{lane_index}"})
            kimi = KimiClient(lane_cfg, rate_bucket=self._bucket, halt=self._halt)
            try:
                kimi.navigate("https://www.youtube.com", new_tab=True)
            except (BridgeNotConnected, httpx.ConnectError, httpx.TimeoutException) as exc:
                # Can't even open a tab → a fatal browser-setup problem that hits
                # every lane identically. Record one clean message, halt the run,
                # and exit quietly (no traceback spam from N lanes).
                self._set_setup_error(exc)
                return
            while not self._halt.is_set():
                if self._breaker.should_retire(lane_index):
                    log.info("lane %d retiring (circuit breaker)", lane_index)
                    return
                try:
                    item = self._work_q.get_nowait()
                except queue.Empty:
                    return
                if self.state.is_done(item.id):
                    continue
                try:
                    bundle = self._restore_or_extract(kimi, item)
                    self._queue.put((item, bundle))
                    with self._count_lock:           # extraction done for this item
                        self.stats.extracted += 1
                except CaptchaInterstitial as exc:
                    if self._handle_captcha(lane_index, exc):
                        return                    # this lane retires/halts
                    # not halting and not retiring → requeue item for a sibling
                    self._work_q.put(item)
                    return
                except BridgeNotConnected as exc:
                    # extension dropped mid-run → halt cleanly, requeue this item
                    self._set_setup_error(exc)
                    self._work_q.put(item)
                    return
                except Exception as exc:
                    log.warning("extraction failed for %s: %s", item.id, exc)
                    self._queue.put((item, exc))
                kimi.item_pause()
        except Exception:
            log.exception("lane %d failed", lane_index)
        finally:
            if kimi is not None:
                try:
                    kimi.close()
                except Exception:
                    log.warning("kimi.close() failed for lane %d", lane_index, exc_info=True)

    def _set_setup_error(self, exc: Exception) -> None:
        """Record a fatal browser-setup failure once and halt all lanes."""
        from ytqc.browser.webbridge import BridgeNotConnected
        if isinstance(exc, BridgeNotConnected):
            msg = ("browser extension not connected — open Chrome with the "
                   "kimi-webbridge extension active (its background worker may have "
                   "gone idle; click the extension / focus the window), then retry")
        else:
            msg = f"can't reach the browser bridge ({type(exc).__name__}) — is kimi-webbridge running?"
        with self._sink_lock:
            if self._setup_error is None:
                self._setup_error = msg
                self.console.print(f"\n[err]✗ {msg}[/]")
        self._halt.set()

    def _handle_captcha(self, lane_index: int, exc: Exception) -> bool:
        """Record the stress signal, slow the request rate, and decide: graceful
        lane-retire (return True, keep siblings running) or global halt. Returns
        True if this lane should stop."""
        level = self._breaker.record_stress("captcha")
        # slow the aggregate rate proportionally to stress
        self._bucket.set_rate(max(self.cfg.pipeline.max_pages_per_min // (level + 1), 2))
        degrade = self.cfg.pipeline.degrade_on_captcha
        if (not degrade) or self._breaker.allowed_lanes <= self.cfg.pipeline.min_lane_count:
            # at the floor (or breaker off) → stop everything and checkpoint
            self._halt.set()
            with self._sink_lock:
                if not self._halt_announced:
                    self._halt_announced = True
                    log.error("CAPTCHA interstitial — halting run: %s", exc)
                    self.console.print(
                        "[red bold]Bot-check interstitial detected — run halted and "
                        "checkpointed. Solve it in the browser, then `ytqc resume`.[/]")
            return True
        log.warning("captcha on lane %d (stress level %d) — retiring lane, "
                    "throttling; %d lanes remain", lane_index, level,
                    self._breaker.allowed_lanes)
        return True

    def _restore_or_extract(self, kimi: KimiClient, item: InputItem):
        cls = VideoExtract if item.type == "video" else ChannelExtract

        # 1. per-run artifact (resume of THIS run)
        saved = self.state.load_artifact(item.id, "extracted.json")
        if saved:
            try:
                bundle = cls.model_validate(saved)
                log.info("reusing saved extraction for %s", item.id)
                return bundle
            except Exception:
                log.warning("saved extraction for %s failed to load — re-extracting", item.id)

        # 2. cross-run extraction cache (TTL'd) — skip re-scraping a recent id.
        #    Only cache OK extracts; still write the per-run artifact so resume works.
        cache_key = ExtractCache.make_key(item.id, item.type)
        cached = self._extract_cache.get(cache_key, item.type)
        if cached:
            try:
                bundle = cls.model_validate(cached)
                log.info("reusing cross-run cached extraction for %s", item.id)
                self.state.mark(item.id, "EXTRACTED")
                self.state.save_artifact(item.id, "extracted.json", bundle.model_dump())
                return bundle
            except Exception:
                log.warning("cross-run cached extraction for %s failed to load — re-extracting", item.id)

        if item.type == "video":
            bundle = extract_video(kimi, item.id, self.cfg.sampling, depth="full",
                                   with_comments=self.comments,
                                   overlap_comments=self.cfg.pipeline.overlap_comment_load,
                                   with_vidiq=self.cfg.pipeline.vidiq_scrape,
                                   vidiq_timeout_s=self.cfg.pipeline.vidiq_timeout_s)
        else:
            # Channel QC now classifies from the scrolled catalog (titles + grid
            # thumbnails), not from deep-sampled videos — no _extract_samples call.
            bundle = extract_channel(kimi, item.id,
                                     with_vidiq=self.cfg.pipeline.vidiq_scrape,
                                     vidiq_timeout_s=self.cfg.pipeline.vidiq_timeout_s,
                                     channel_pages=self.cfg.sampling.channel_pages,
                                     channel_grid_shots=self.cfg.sampling.channel_grid_shots)
        self.state.mark(item.id, "EXTRACTED")
        payload = bundle.model_dump()
        self.state.save_artifact(item.id, "extracted.json", payload)
        if getattr(bundle, "ok", True):          # don't cache failed/partial extracts
            self._extract_cache.put(cache_key, item.type, payload)
        return bundle

    # ── analysis worker: LLM + sink ───────────────────────────────────────
    def _consume(self, progress: Progress, task_id) -> None:
        try:
            while True:
                got = self._queue.get()
                if got is _SENTINEL:
                    return
                item, bundle = got
                try:
                    rec = self._analyze(item, bundle)
                except Exception as exc:
                    log.exception("flow failed for %s", item.id)
                    rec = QCRecord(id=item.id, type=item.type, status="ERROR",
                                   error=str(exc)[:300], needs_review=True, confidence=0.0,
                                   run_id=self.state.run_id)
                desc = self._desc(rec)            # pure formatting — no lock needed
                try:
                    # hold the lock only for the genuinely-shared writes (sinks,
                    # state file, stats); the progress bar has its own internal
                    # lock, so advance/update run outside to shorten contention.
                    with self._sink_lock:
                        for sink in self.sinks:
                            sink.write(rec)
                        self.state.mark(item.id, "SUNK")
                        self._tally(rec)
                    progress.advance(task_id)
                    progress.update(task_id, description=desc)
                except Exception:
                    log.exception("sink/state write failed for %s", item.id)
        finally:
            self._queue.put(_SENTINEL)        # belt-and-suspenders for siblings

    def _analyze(self, item: InputItem, bundle) -> QCRecord:
        if isinstance(bundle, Exception):
            return QCRecord(id=item.id, type=item.type, status="ERROR",
                            error=str(bundle)[:300], needs_review=True, confidence=0.0,
                            run_id=self.state.run_id)
        if self.extract_only:
            rec = QCRecord(id=item.id, type=item.type, run_id=self.state.run_id,
                           name=getattr(bundle, "title", ""))
            rec.comment = "extract-only run"
            if getattr(bundle, "ok", True) is False:
                rec.status = "ERROR"
                rec.error = getattr(bundle, "error", "")
                rec.confidence = 0.0
                rec.needs_review = True
            return rec
        if item.type == "video":
            rec = run_video_flow(self.llm, bundle, self.state.run_id)
        else:
            rec = run_channel_flow(self.llm, bundle, self.state.run_id,
                                   analysis_workers=self.cfg.pipeline.analysis_workers)
        rec.needs_review = rec.needs_review or rec.confidence < self.cfg.pipeline.review_threshold
        return rec

    def _tally(self, rec: QCRecord) -> None:
        self.stats.done += 1
        if rec.status == "ERROR":
            self.stats.errors += 1
        if rec.brand_safety_is_safe is False:
            self.stats.unsafe += 1
        if rec.needs_review:
            self.stats.needs_review += 1
        if rec.tier_1:
            self.stats.tier_counts[rec.tier_1] = self.stats.tier_counts.get(rec.tier_1, 0) + 1

    def _desc(self, rec: QCRecord) -> str:
        tag = rec.tier_1 or rec.status
        return f"[cyan]{rec.id[:24]}[/] → {tag}"

    # ── entry ─────────────────────────────────────────────────────────────
    def run(self) -> RunStats:
        todo = [i for i in self.items if not self.state.is_done(i.id)]
        skipped = len(self.items) - len(todo)
        if skipped:
            self.console.print(f"[dim]{skipped} item(s) already done — skipped (resume)[/]")
        if not todo:
            return self.stats
        for item in todo:
            self._work_q.put(item)
        n_lanes = min(self.lanes, len(todo))

        t0 = time.time()
        # Drive the display ourselves: a single Live wraps a Group of [rotating
        # Hinglish status line] + [progress bar]. Progress(auto_refresh=False)
        # so worker threads only mutate task state; the Live thread renders.
        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=self.console,
            auto_refresh=False,
        )
        task_id = progress.add_task("starting…", total=len(todo))

        status_state = {"frame": 0, "phrase_idx": 0}
        status_stop = threading.Event()

        total = len(todo)

        def _status_group():
            if status_stop.is_set():
                return progress.get_renderable()      # phrase clears, bar stays on finish
            phrase = ui.pick_verb(status_state["phrase_idx"])
            line = ui.run_status_line(status_state["frame"], phrase,
                                      self.stats.extracted, self.stats.done, total,
                                      self._browser_done.is_set())
            return Group(Text(line, style="bullet"), progress.get_renderable())

        def _tick_status():
            # ~12fps glyph animation; rotate the phrase every ~3.4s
            while not status_stop.wait(0.12):
                status_state["frame"] += 1
                if status_state["frame"] % 28 == 0:
                    status_state["phrase_idx"] += 1

        live = Live(get_renderable=_status_group, console=self.console,
                    refresh_per_second=12, transient=False)
        ticker = threading.Thread(target=_tick_status, daemon=True, name="status-ticker")

        interrupted = False
        live.start()
        ticker.start()
        try:
            workers = [
                threading.Thread(target=self._consume, args=(progress, task_id),
                                 daemon=True, name=f"analysis-{i}")
                for i in range(self.workers)
            ]
            self._worker_threads = workers
            for w in workers:
                w.start()
            lanes = [
                threading.Thread(target=self._lane, args=(i,), daemon=True, name=f"lane-{i}")
                for i in range(n_lanes)
            ]
            for ln in lanes:
                ln.start()
            try:
                # producer barrier: all lanes finish before we close the bundle stream
                for ln in lanes:
                    ln.join()
                self._browser_done.set()        # all tabs closed → extraction phase over
                for _ in workers:
                    self._put_sentinel()
                for w in workers:
                    w.join()
            except KeyboardInterrupt:
                # Ctrl-C lands on the main thread; the lane/worker daemons don't
                # get it. Tear down gracefully: signal halt, close the browser
                # tabs by session name, then best-effort join so threads unwind
                # their own finally blocks. State is already checkpointed.
                interrupted = True
                self.console.print("\n[yellow]interrupting — closing browser tabs…[/]")
                self._halt.set()
                self._shutdown_lanes(n_lanes)
                for _ in workers:
                    self._put_sentinel()
                for ln in lanes:
                    ln.join(timeout=5)
                for w in workers:
                    w.join(timeout=8)
        finally:
            # stop the animation first (normal, Ctrl-C, and error paths), then
            # the Live, so nothing is still drawing when we print summaries.
            status_stop.set()
            ticker.join(timeout=0.5)
            live.stop()

        if interrupted:
            self.console.print(
                f"[yellow]interrupted[/] — {self.stats.done} item(s) done & checkpointed. "
                f"Resume with [bold]ytqc resume {self.state.run_id} --input <file>[/]")
            raise KeyboardInterrupt

        elapsed = time.time() - t0
        rate = self.stats.done / elapsed * 3600 if elapsed > 0 else 0
        self.console.print(
            f"\n[bold]{self.stats.done}[/] items in {elapsed/60:.1f} min "
            f"(~{rate:.0f}/hr) | lanes: {n_lanes} workers: {self.workers} "
            f"| errors: {self.stats.errors} | unsafe: {self.stats.unsafe} "
            f"| needs_review: {self.stats.needs_review} | llm calls: {self.llm.calls} "
            f"| cache hits: {self.llm.cache_hits}"
        )
        if self.stats.tier_counts:
            dist = ", ".join(f"{k}: {v}" for k, v in
                             sorted(self.stats.tier_counts.items(), key=lambda kv: -kv[1]))
            self.console.print(f"[dim]tier_1 distribution: {dist}[/]")
        return self.stats
