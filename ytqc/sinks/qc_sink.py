"""The QC team's Automation QC output file — ONE csv, twelve columns, in the
order the QC sheet expects. This is the deliverable sink (default); the wide
`csv`/`xlsx` sinks stay available for debugging via --sink.

Every column is projected from an already-validated QCRecord — no new judgement
happens here, so the file can always be regenerated from the run's artifacts.
Appends per row and dedupes on close, so a resumed run produces one clean file.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from ytqc.models import QCRecord
from ytqc.sinks.base import ResultSink
from ytqc.utils.csv_safe import csv_safe

FILENAME = "qc_output.csv"

# Header text is the QC team's, verbatim and in their order — downstream sheets
# key off these names, so don't rename or reorder them.
COLUMNS = [
    "Channel ID",
    "Title",
    "Subscribers",
    "Channel Country",
    "Brand Safety Status",
    "Brand Safety Risk Level",
    "Tier 1",
    "Tier 2",
    "Age Marking",
    "Language",
    "Shorts",
    "Premium",
]

_YES_NO = {True: "Yes", False: "No"}


def _age_marking(rec: QCRecord) -> str:
    """Kids content carries the kids band ("6-8 years"); everything else carries
    the suitability band ("all ages" / "13+" / "16+" / "18+")."""
    if rec.kids_age_group:
        return rec.kids_age_group
    band = (rec.suitable_age_group or "").strip()
    return band.title() if band == "all ages" else band


def _safety_status(rec: QCRecord) -> str:
    if rec.status == "ERROR":
        return "Error"
    if rec.brand_safety_is_safe is None:
        return ""
    return "Safe" if rec.brand_safety_is_safe else "Unsafe"


def to_qc_row(rec: QCRecord) -> dict[str, str]:
    """Project a QCRecord onto the twelve QC-output columns."""
    risk = (rec.brand_safety_risk_level or "").strip()
    row = {
        "Channel ID": rec.id,
        "Title": rec.name or "",
        "Subscribers": rec.subscribers or 0,
        "Channel Country": rec.country or "",
        "Brand Safety Status": _safety_status(rec),
        "Brand Safety Risk Level": risk.title() if rec.status != "ERROR" else "",
        "Tier 1": rec.tier_1 or "",
        "Tier 2": rec.tier_2 or "",
        "Age Marking": _age_marking(rec),
        "Language": rec.language or "",
        "Shorts": _YES_NO[bool(rec.is_shorts)],
        "Premium": _YES_NO[bool(rec.is_premium_luxury)],
    }
    # Spreadsheet formula-injection guard. Channel ID is this sheet's join key,
    # so a plain @handle is left intact there (see ytqc.utils.csv_safe).
    return {k: csv_safe(v, id_column=(k == "Channel ID")) for k, v in row.items()}


class QcCsvSink(ResultSink):
    """Appends one QC row per item (flushed immediately, so a killed run keeps
    every finished row) and dedupes by Channel ID on close."""

    def __init__(self):
        self._fh = None
        self._writer = None
        self.path: Path | None = None

    def open(self, run_id: str, output_dir: str) -> None:
        self.path = Path(output_dir) / run_id / FILENAME
        self.path.parent.mkdir(parents=True, exist_ok=True)
        exists = self.path.exists()
        self._fh = self.path.open("a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=COLUMNS)
        if not exists:
            self._writer.writeheader()

    def write(self, record: QCRecord) -> None:
        self._writer.writerow(to_qc_row(record))
        self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None
            self._writer = None
        # Resume-safe de-dup: re-QC'd ids (a resumed run, a re-run into the same
        # run dir) leave more than one row per channel. Keep the LAST.
        if self.path is None or not self.path.exists():
            return
        df = pd.read_csv(self.path, dtype=str, keep_default_na=False)
        if df.empty or "Channel ID" not in df.columns:
            return
        df = df.drop_duplicates(subset="Channel ID", keep="last")
        df.reindex(columns=COLUMNS).to_csv(
            self.path, index=False, columns=COLUMNS, encoding="utf-8")
