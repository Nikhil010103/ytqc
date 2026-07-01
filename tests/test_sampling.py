from ytqc.sampling import TimedSegment, format_excerpts, plan_sampling


def segs_every(duration, step=6.0):
    t, out = 0.0, []
    while t < duration:
        out.append(TimedSegment(t, f"words at {int(t)}"))
        t += step
    return out


def total_target(windows):
    return sum(w.span for w in windows)


def test_short_video_takes_most_of_it():
    w = plan_sampling(30, segs_every(30))
    assert 1 <= len(w) <= 2
    assert total_target(w) <= 31


def test_5min_video_hits_minimum_60s():
    w = plan_sampling(300, segs_every(300))
    # 25% of 300 = 75s — within [60, 120]
    assert 60 <= total_target(w) <= 120.5
    assert len(w) == 5


def test_30min_video_capped_at_120s():
    w = plan_sampling(1800, segs_every(1800))
    assert total_target(w) <= 121
    # coverage drops to ~6.7% — long-video taper per spec
    assert total_target(w) / 1800 < 0.10


def test_2h_video_capped_and_spread():
    w = plan_sampling(7200, segs_every(7200))
    assert total_target(w) <= 121
    labels = [x.label for x in w]
    assert labels[0] == "intro" and labels[-1] == "outro"
    assert w[-1].frame_t > 6000          # outro frame genuinely late


def test_frames_align_with_windows():
    w = plan_sampling(814, segs_every(814))
    for win in w:
        assert win.start_s <= win.frame_t <= win.end_s


def test_no_transcript_still_yields_frame_timestamps():
    w = plan_sampling(600, None)
    assert len(w) == 5
    assert all(x.text == "" for x in w)


def test_excerpt_block_format():
    w = plan_sampling(300, segs_every(300))
    block = format_excerpts(w, 300)
    assert block.startswith("== TRANSCRIPT EXCERPTS")
    assert "[intro" in block and "[outro" in block
