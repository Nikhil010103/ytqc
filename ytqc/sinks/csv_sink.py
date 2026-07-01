"""CSV sink — appends per row (resume-safe)."""
from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from ytqc.models import QCRecord
from ytqc.sinks.base import ResultSink

COLUMNS = list(QCRecord.model_fields.keys())


class CsvSink(ResultSink):
    def __init__(self):
        self._fh = None
        self._writer = None
        self.path: Path | None = None

    def open(self, run_id: str, output_dir: str) -> None:
        self.path = Path(output_dir) / run_id / "results.csv"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        exists = self.path.exists()
        self._fh = self.path.open("a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=COLUMNS)
        if not exists:
            self._writer.writeheader()

    def write(self, record: QCRecord) -> None:
        self._writer.writerow(record.to_flat_dict())
        self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None
            self._writer = None
        # Resume-safe de-dup: appends across resume attempts can leave duplicate
        # rows for the same id. Keep only the LAST row per id and rewrite the
        # file, preserving header + column order so results.csv stays authoritative.
        if self.path is None or not self.path.exists():
            return
        df = pd.read_csv(self.path, dtype=str, keep_default_na=False)
        if df.empty or "id" not in df.columns:
            return
        df = df.drop_duplicates(subset="id", keep="last")
        df = df.reindex(columns=COLUMNS)
        df.to_csv(self.path, index=False, columns=COLUMNS, encoding="utf-8")
