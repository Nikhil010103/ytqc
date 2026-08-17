"""The Automation QC output file (ytqc/sinks/qc_sink.py).

This is the one file the QC team consumes, so these tests pin its shape: the
twelve column headers, in order, and how each QCRecord field is projected onto
them (Yes/No flags, safe/unsafe wording, the Age Marking pick, error rows).
"""
from __future__ import annotations

import csv

import pytest

from ytqc.models import QCRecord
from ytqc.sinks.base import build_sinks
from ytqc.sinks.qc_sink import COLUMNS, FILENAME, QcCsvSink, to_qc_row

RUN_ID = "run-2026"


def _rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _channel(**over) -> QCRecord:
    base = dict(
        id="UC1234567890abcdefABCD12", type="channel", name="MotoGarage",
        subscribers=1_200_000, country="United States",
        brand_safety_is_safe=True, brand_safety_risk_level="none",
        tier_1="Automobile", tier_2="motorcycle reviews",
        suitable_age_group="all ages", language="en",
    )
    base.update(over)
    return QCRecord(**base)


def test_columns_are_the_qc_sheet_headers_in_order():
    assert COLUMNS == [
        "Channel ID", "Title", "Subscribers", "Channel Country",
        "Brand Safety Status", "Brand Safety Risk Level", "Tier 1", "Tier 2",
        "Age Marking", "Language", "Shorts", "Premium",
    ]


def test_row_projection_safe_channel():
    row = to_qc_row(_channel())
    assert row["Channel ID"] == "UC1234567890abcdefABCD12"
    assert row["Title"] == "MotoGarage"
    assert row["Subscribers"] == 1_200_000
    assert row["Channel Country"] == "United States"
    assert row["Brand Safety Status"] == "Safe"
    assert row["Brand Safety Risk Level"] == "None"
    assert row["Tier 1"] == "Automobile"
    assert row["Tier 2"] == "motorcycle reviews"
    assert row["Age Marking"] == "All Ages"
    assert row["Language"] == "en"
    assert row["Shorts"] == "No"
    assert row["Premium"] == "No"


def test_unsafe_channel_status_and_risk():
    row = to_qc_row(_channel(brand_safety_is_safe=False,
                             brand_safety_risk_level="high",
                             suitable_age_group="18+"))
    assert row["Brand Safety Status"] == "Unsafe"
    assert row["Brand Safety Risk Level"] == "High"
    assert row["Age Marking"] == "18+"


def test_kids_channel_carries_the_kids_band_as_age_marking():
    row = to_qc_row(_channel(tier_1="Kids", kids_age_group="3-5 years",
                             suitable_age_group="all ages"))
    assert row["Age Marking"] == "3-5 years"


def test_shorts_and_premium_are_yes_no():
    row = to_qc_row(_channel(is_shorts=True, is_premium_luxury=True))
    assert row["Shorts"] == "Yes"
    assert row["Premium"] == "Yes"


def test_error_row_still_written_and_labelled():
    """A failed extraction must appear in the file — the QC team needs to see
    which ids didn't get a verdict, not have them silently missing."""
    row = to_qc_row(QCRecord(id="UCdead", type="channel", status="ERROR",
                             error="channel not found"))
    assert row["Channel ID"] == "UCdead"
    assert row["Brand Safety Status"] == "Error"
    assert row["Brand Safety Risk Level"] == ""
    assert row["Tier 1"] == "" and row["Age Marking"] == ""


def test_formula_injection_is_escaped():
    row = to_qc_row(_channel(name="=cmd|'/c calc'!A1"))
    assert row["Title"].startswith("'=")


def test_at_handle_id_is_left_intact_for_lookups():
    """Channel ID is the QC sheet's join key — escaping `@mrbeast` to
    `'@mrbeast` would break every VLOOKUP against the team's other sheets."""
    assert to_qc_row(_channel(id="@mrbeast"))["Channel ID"] == "@mrbeast"
    assert to_qc_row(_channel(id="@Mr.Beast_6-000"))["Channel ID"] == "@Mr.Beast_6-000"


@pytest.mark.parametrize("value", [
    "@SUM(1+1)*cmd|' /C calc'!A0",     # @ followed by a real call → still dangerous
    "=1+1",
    "+1+1",
    "-1+1",
    "@handle with spaces",
])
def test_dangerous_ids_are_still_escaped(value):
    assert to_qc_row(_channel(id=value))["Channel ID"].startswith("'")


def test_at_leading_value_outside_the_id_column_is_still_escaped():
    assert to_qc_row(_channel(name="@mrbeast"))["Title"] == "'@mrbeast"


def test_sink_writes_one_file_with_header_and_rows(tmp_path):
    sink = QcCsvSink()
    sink.open(RUN_ID, str(tmp_path))
    sink.write(_channel(id="UCa", name="A"))
    sink.write(_channel(id="UCb", name="B", is_shorts=True))
    sink.close()

    run_dir = tmp_path / RUN_ID
    assert [p.name for p in run_dir.iterdir() if p.suffix == ".csv"] == [FILENAME]
    rows = _rows(run_dir / FILENAME)
    assert [r["Channel ID"] for r in rows] == ["UCa", "UCb"]
    assert rows[1]["Shorts"] == "Yes"
    assert list(rows[0].keys()) == COLUMNS


def test_rows_survive_a_kill_before_close(tmp_path):
    """Incremental checkpointing: every row is flushed as it is written, so a
    process killed mid-run still leaves the finished channels on disk."""
    sink = QcCsvSink()
    sink.open(RUN_ID, str(tmp_path))
    sink.write(_channel(id="UCa"))
    sink.write(_channel(id="UCb"))
    # no close() — simulate SIGKILL
    assert len(_rows(tmp_path / RUN_ID / FILENAME)) == 2


def test_resume_appends_then_dedupes_keeping_last(tmp_path):
    first = QcCsvSink()
    first.open(RUN_ID, str(tmp_path))
    first.write(_channel(id="UCa", tier_1="Music"))
    first.write(_channel(id="UCb"))
    first.close()

    resumed = QcCsvSink()                       # second process, same run dir
    resumed.open(RUN_ID, str(tmp_path))
    resumed.write(_channel(id="UCa", tier_1="Gaming"))   # re-QC'd
    resumed.write(_channel(id="UCc"))
    resumed.close()

    rows = _rows(tmp_path / RUN_ID / FILENAME)
    assert [r["Channel ID"] for r in rows] == ["UCb", "UCa", "UCc"]
    assert next(r for r in rows if r["Channel ID"] == "UCa")["Tier 1"] == "Gaming"


def test_qc_is_the_default_sink_and_is_registered():
    from ytqc.config import YtqcConfig
    assert YtqcConfig().sinks == ["qc"]
    assert isinstance(build_sinks(["qc"])[0], QcCsvSink)
