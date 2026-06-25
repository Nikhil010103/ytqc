from ytqc.agents.safety_gate import enforce_floor, hits_block, scan


def test_scan_finds_word_boundary_hits():
    hits = scan({"title": "Best casino bonuses and free spins!", "description": ""})
    groups = {h.group for h in hits}
    assert "gambling" in groups


def test_scan_no_substring_false_positives():
    # 'winery' must not hit 'wine'; 'scattering' must not hit anything
    hits = scan({"title": "Tuscany winery tour, light scattering physics"})
    assert all(h.term != "wine" for h in hits)


def test_scan_dedupes_per_group_per_field():
    hits = scan({"title": "casino poker betting jackpot"})
    gambling = [h for h in hits if h.group == "gambling"]
    assert len(gambling) == 1


def test_enforce_floor_raises_acknowledged_hit():
    hits = scan({"title": "live casino betting stream"})
    risk, cats = enforce_floor("low", ["Gambling"], hits)
    assert risk == "medium"            # floor from the gambling group


def test_enforce_floor_unacknowledged_makes_visible():
    hits = scan({"transcript": "we visited a casino"})
    risk, cats = enforce_floor("none", [], hits)
    assert risk == "low"               # visible in review, not silently none


def test_llm_can_keep_higher_risk():
    hits = scan({"title": "wine tasting"})
    risk, _ = enforce_floor("high", ["Alcohol"], hits)
    assert risk == "high"


def test_hits_block_renders():
    hits = scan({"title": "casino night"})
    block = hits_block(hits)
    assert "casino" in block and "false positive" in block
    assert hits_block([]) == ""
