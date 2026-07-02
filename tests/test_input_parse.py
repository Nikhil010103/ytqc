"""Deterministic input normalization — pure, no I/O, no monkeypatch."""
from ytqc.input_parse import detect_id_column, items_from_rows, parse_items

PASTE = """UCECWJfpmSWeaZ2fbb0rlq_g,channel,US - Noodah05
UC7trU46U_9XPDtMnDbiDPUQ,channel,US - JEV
UCLbdVvreihwZRL6kwuEUYsA,channel,IN - Think Music India
UCugG6-k5QGbq_iDEPAnG4NQ,channel,IN - KRAFTON INDIA ESPORTS
UCdPsNbQIs6U36fyMdkzOvbQ,channel,IN - Navaan Sandhu"""


def test_the_reported_paste_yields_five_channels():
    items, rep = parse_items(PASTE, default_type="channel")
    assert len(items) == 5
    assert {i.type for i in items} == {"channel"}
    assert items[0].id == "UCECWJfpmSWeaZ2fbb0rlq_g"
    assert items[0].label == "US - Noodah05"          # commas/hyphens preserved
    assert all(i.id.startswith("UC") for i in items)  # noise never becomes an id
    assert rep.unrecognized == [] and rep.n_deduped == 0
    assert rep.channels == 5 and rep.videos == 0


def test_label_with_commas_kept_whole():
    items, _ = parse_items("UCECWJfpmSWeaZ2fbb0rlq_g,channel,US - Noo, dah", "channel")
    assert len(items) == 1
    assert items[0].label == "US - Noo, dah"


def test_duplicate_ids_deduped_with_label_backfill():
    text = ("UCECWJfpmSWeaZ2fbb0rlq_g,channel,\n"
            "UCECWJfpmSWeaZ2fbb0rlq_g,channel,US - Noodah05")
    items, rep = parse_items(text, "channel")
    assert len(items) == 1
    assert rep.n_deduped == 1
    assert items[0].label == "US - Noodah05"          # back-filled from 2nd line


def test_bare_name_line_is_unrecognized():
    items, rep = parse_items("US - Noodah05", "channel")
    assert items == []
    assert rep.unrecognized == ["US - Noodah05"]


def test_literal_word_channel_is_not_an_id():
    items, rep = parse_items("channel", "channel")
    assert items == []
    assert rep.unrecognized == ["channel"]


def test_handle_is_channel():
    for text in ("@Noodah05", "https://www.youtube.com/@Noodah05"):
        items, _ = parse_items(text, None)
        assert len(items) == 1 and items[0].type == "channel"
        assert items[0].id == "@Noodah05"


def test_channel_url_is_channel():
    items, _ = parse_items("https://www.youtube.com/channel/UCECWJfpmSWeaZ2fbb0rlq_g", None)
    assert len(items) == 1
    assert items[0].id == "UCECWJfpmSWeaZ2fbb0rlq_g" and items[0].type == "channel"


def test_watch_url_is_video():
    items, _ = parse_items("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "channel")
    assert len(items) == 1
    assert items[0].id == "dQw4w9WgXcQ" and items[0].type == "video"   # URL forces video


def test_youtu_be_and_shorts_are_videos():
    items, _ = parse_items("https://youtu.be/dQw4w9WgXcQ\nyoutube.com/shorts/abc_def-123", None)
    assert {i.type for i in items} == {"video"}
    assert {i.id for i in items} == {"dQw4w9WgXcQ", "abc_def-123"}


def test_bare_11char_uses_default_type():
    items, _ = parse_items("dQw4w9WgXcQ", "video")
    assert len(items) == 1 and items[0].type == "video"
    items2, _ = parse_items("dQw4w9WgXcQ", None)       # no default → shape falls back to video
    assert items2[0].type == "video"


def test_uc_id_not_also_matched_as_video():
    items, _ = parse_items("UCECWJfpmSWeaZ2fbb0rlq_g", "channel")
    assert len(items) == 1                             # NOT also an 11-char video
    assert items[0].type == "channel"


def test_crlf_normalized():
    items, rep = parse_items(PASTE.replace("\n", "\r\n"), "channel")
    assert len(items) == 5 and rep.unrecognized == []
    assert "\r" not in items[0].label


def test_trailing_comma_id_only():
    items, _ = parse_items("UCECWJfpmSWeaZ2fbb0rlq_g,channel,", "channel")
    assert len(items) == 1 and items[0].label is None and items[0].type == "channel"


def test_header_row_skipped():
    items, _ = parse_items("id,type,label\nUCECWJfpmSWeaZ2fbb0rlq_g,channel,Noo", "channel")
    assert len(items) == 1 and items[0].id == "UCECWJfpmSWeaZ2fbb0rlq_g"


def test_mixed_channel_and_video():
    text = "UCECWJfpmSWeaZ2fbb0rlq_g,channel,A\nhttps://youtu.be/dQw4w9WgXcQ"
    items, rep = parse_items(text, "channel")
    assert rep.channels == 1 and rep.videos == 1


def test_single_line_comma_list_extracts_all():
    # "UC1, UC2, UC3" on one line is a LIST, not an id,type,label row.
    text = "UCECWJfpmSWeaZ2fbb0rlq_g, UC7trU46U_9XPDtMnDbiDPUQ, UCLbdVvreihwZRL6kwuEUYsA"
    items, rep = parse_items(text, "channel")
    assert len(items) == 3
    assert {i.type for i in items} == {"channel"}
    assert rep.unrecognized == []


# ── spreadsheet id-column auto-detection (files as smart as paste) ────────────

def test_detect_literal_id_column_always_wins():
    rows = [{"id": "UCECWJfpmSWeaZ2fbb0rlq_g", "name": "Noodah05"}]
    assert detect_id_column(["id", "name"], rows) == "id"


def test_detect_id_column_by_content_url():
    rows = [
        {"name": "Noodah05", "channel url": "https://www.youtube.com/channel/UCECWJfpmSWeaZ2fbb0rlq_g"},
        {"name": "JEV", "channel url": "https://www.youtube.com/channel/UC7trU46U_9XPDtMnDbiDPUQ"},
    ]
    assert detect_id_column(["name", "channel url"], rows) == "channel url"


def test_detect_id_column_handles_and_bare_ids():
    rows = [{"who": "@Noodah05"}, {"who": "UCECWJfpmSWeaZ2fbb0rlq_g"}]
    assert detect_id_column(["who"], rows) == "who"


def test_detect_id_column_none_when_no_ids():
    rows = [{"name": "Noodah05", "notes": "cool channel"},
            {"name": "JEV", "notes": "gaming"}]
    assert detect_id_column(["name", "notes"], rows) is None


def test_items_from_rows_normalizes_urls_handles_bare():
    rows = [
        {"channel url": "https://www.youtube.com/channel/UCECWJfpmSWeaZ2fbb0rlq_g"},
        {"channel url": "https://www.youtube.com/@Noodah05"},
        {"channel url": "UC7trU46U_9XPDtMnDbiDPUQ"},
    ]
    items, rep = items_from_rows(rows, "channel url", default_type="channel")
    assert {i.id for i in items} == {
        "UCECWJfpmSWeaZ2fbb0rlq_g", "@Noodah05", "UC7trU46U_9XPDtMnDbiDPUQ"}
    assert {i.type for i in items} == {"channel"}      # UC + handle are channels
    assert rep.channels == 3 and rep.videos == 0


def test_items_from_rows_honors_type_and_label_columns():
    rows = [{"link": "dQw4w9WgXcQ", "type": "video", "label": "Rick"}]
    items, _ = items_from_rows(rows, "link", type_col="type", label_col="label",
                               default_type="channel")
    assert len(items) == 1
    assert items[0].id == "dQw4w9WgXcQ" and items[0].type == "video"
    assert items[0].label == "Rick"


def test_items_from_rows_dedupes_and_reports_unrecognized():
    rows = [
        {"col": "UCECWJfpmSWeaZ2fbb0rlq_g"},
        {"col": "UCECWJfpmSWeaZ2fbb0rlq_g"},            # duplicate
        {"col": "just a name, no id"},                  # no id → unrecognized (strict)
    ]
    items, rep = items_from_rows(rows, "col", default_type="channel")
    assert len(items) == 1 and rep.n_deduped == 1
    assert rep.unrecognized == ["just a name, no id"]


def test_items_from_rows_trust_raw_keeps_noncanonical_id():
    # An explicit 'id' column is taken as given (back-compat): a non-canonical
    # value like 'v1' is kept verbatim rather than dropped as unrecognized.
    rows = [{"id": "v1", "type": "video"}]
    items, rep = items_from_rows(rows, "id", type_col="type", trust_raw=True)
    assert len(items) == 1 and items[0].id == "v1" and items[0].type == "video"
    assert rep.unrecognized == []
