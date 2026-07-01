"""Adaptive transcript/frame sampling.

One function drives BOTH transcript excerpting and frame timestamps so the
visual evidence aligns with the speech evidence. Spec (user-defined):
cover ~20-30% of the video but normalized to ~60-120s of speech — long videos
get a lower percentage, short videos a higher one. Frames are taken at the
midpoints of the sampled windows, re-centered onto actual speech when a
transcript exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TimedSegment:
    start_s: float
    text: str


@dataclass
class Window:
    label: str          # intro|early|middle|late|outro
    start_s: float
    end_s: float
    frame_t: float      # timestamp for frame capture (window midpoint)
    text: str = ""      # filled from transcript segments

    @property
    def span(self) -> float:
        return self.end_s - self.start_s


_ANCHORS = (("intro", 0.02), ("early", 0.25), ("middle", 0.50), ("late", 0.75), ("outro", 0.92))


def plan_sampling(
    duration_s: float,
    segments: list[TimedSegment] | None = None,
    target_min: float = 60.0,
    target_max: float = 120.0,
    pct: float = 0.25,
    n_windows: int = 5,
) -> list[Window]:
    D = max(duration_s, 1.0)
    if D <= target_min + 15:                      # Shorts / very short videos
        target = min(D, target_min)
        n_windows = 3 if D > 45 else 2
    else:
        target = min(max(pct * D, target_min), target_max)

    w = target / n_windows
    windows: list[Window] = []
    for label, frac in _ANCHORS[:n_windows]:
        c = frac * D
        s = max(0.0, c - w / 2)
        e = min(D - 0.5, c + w / 2)
        if e <= s:
            continue
        windows.append(Window(label, s, e, frame_t=(s + e) / 2))

    windows = _merge_overlaps(windows)

    if segments:
        for win in windows:
            _fill_speech(win, segments, w)
    return windows


def _merge_overlaps(windows: list[Window]) -> list[Window]:
    if not windows:
        return windows
    merged = [windows[0]]
    for win in windows[1:]:
        prev = merged[-1]
        if win.start_s <= prev.end_s:
            prev.end_s = max(prev.end_s, win.end_s)
            prev.frame_t = (prev.start_s + prev.end_s) / 2
        else:
            merged.append(win)
    return merged


def _fill_speech(win: Window, segments: list[TimedSegment], want_s: float) -> None:
    """Collect segments whose start falls in the window; if speech is sparse
    (music/silence), extend rightward until ~want_s seconds of speech or the
    segment list ends. Re-center frame_t on the first collected segment so the
    frame shows what is being said."""
    inside = [s for s in segments if win.start_s <= s.start_s < win.end_s]
    if not inside:
        after = [s for s in segments if s.start_s >= win.start_s]
        inside = after[: max(3, int(want_s / 6))]
    else:
        approx_secs = len(inside) * 6.0          # ASR segments average ~6s
        if approx_secs < want_s:
            extra = [s for s in segments if s.start_s >= win.end_s]
            inside += extra[: int((want_s - approx_secs) / 6)]
    win.text = " ".join(s.text.strip() for s in inside if s.text.strip())
    if inside:
        win.frame_t = min(max(inside[0].start_s + 2.0, win.start_s), win.end_s)


def format_excerpts(windows: list[Window], duration_s: float) -> str:
    """Render the sampled windows as the prompt block."""
    def mmss(t: float) -> str:
        return f"{int(t // 60)}:{int(t % 60):02d}"

    sampled = sum(w.span for w in windows)
    lines = [f"== TRANSCRIPT EXCERPTS (sampled ~{int(sampled)}s of {int(duration_s)}s) =="]
    for w in windows:
        if w.text:
            lines.append(f"[{w.label} {mmss(w.start_s)}-{mmss(w.end_s)}] {w.text}")
    return "\n".join(lines)
