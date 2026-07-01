"""Phase 3: opt-in comment-load overlap. Verifies ordering without a real browser.

A stub KimiClient records the sequence of bridge calls so we can assert that, when
overlap is ON, the comment lazy-load scroll happens BEFORE transcript/frames, and
when OFF the behavior is the legacy order. Fidelity is unchanged either way.
"""
import ytqc.browser.video_page as vp
from ytqc.config import SamplingConfig


class _RecordingKimi:
    def __init__(self):
        self.events = []

    def navigate(self, url, ready_js=None, new_tab=False):
        self.events.append("navigate")

    def js(self, code):
        # classify the JS probe by a marker so the test is robust to exact text
        if "scrollTo(0, 0)" in code:
            self.events.append("scroll_top")
            return {}
        if code is vp.J.PLAYER_RESPONSE:
            return {"ok": True, "hasVideoDetails": True, "title": "T", "author": "A",
                    "lengthSeconds": "120", "viewCount": "1000", "isFamilySafe": True,
                    "tracks": []}
        if code is vp.J.LIKES:
            return {"likes_text": "1,200"}
        if "__TOP_N__" not in code and code is not vp.J.PLAYER_RESPONSE and code is not vp.J.LIKES:
            # comment-count JS (J.COMMENTS already had __TOP_N__ replaced)
            pass
        self.events.append("comments_js")
        return {"count_text": "50 Comments", "comments": [{"author": "x", "text": "y", "likes": "1"}]}

    def scroll(self, px, settle=1.0):
        self.events.append("scroll")


def _fake_transcript(kimi, *a, **k):
    kimi.events.append("transcript")
    from ytqc.models import TranscriptResult
    return TranscriptResult(source="none"), [10.0, 20.0]


def _fake_frames(kimi, video_id, ts, is_live=False):
    kimi.events.append("frames")
    from ytqc.models import FrameSet
    return FrameSet(method="canvas")


def _run(monkeypatch, overlap):
    monkeypatch.setattr(vp, "fetch_transcript", _fake_transcript)
    monkeypatch.setattr(vp, "capture_frames", _fake_frames)
    k = _RecordingKimi()
    ex = vp.extract_video(k, "vid1", SamplingConfig(), depth="full",
                          with_comments=True, overlap_comments=overlap)
    return k.events, ex


def test_overlap_on_primes_comments_before_transcript_and_frames(monkeypatch):
    events, ex = _run(monkeypatch, overlap=True)
    # a scroll (comment prime) happens before transcript and frames
    first_scroll = events.index("scroll")
    assert first_scroll < events.index("transcript") < events.index("frames")
    # fidelity intact: comments still harvested
    assert ex.comments.count == 50 and len(ex.comments.top_comments) == 1


def test_overlap_off_is_legacy_order(monkeypatch):
    events, ex = _run(monkeypatch, overlap=False)
    # legacy: transcript + frames happen BEFORE any comment scroll
    assert events.index("transcript") < events.index("frames") < events.index("scroll")
    assert ex.comments.count == 50 and len(ex.comments.top_comments) == 1
