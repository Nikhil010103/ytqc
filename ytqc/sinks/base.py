"""ResultSink ABC. Future PostgresSink: upsert into channel_curation_manual
keyed on channelid (the QC team's source-of-truth table)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ytqc.models import QCRecord


class ResultSink(ABC):
    @abstractmethod
    def open(self, run_id: str, output_dir: str) -> None: ...

    @abstractmethod
    def write(self, record: QCRecord) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


def build_sinks(names: list[str]) -> list[ResultSink]:
    from ytqc.sinks.csv_sink import CsvSink
    from ytqc.sinks.es_sink import ElasticsearchSink
    from ytqc.sinks.excel_sink import ExcelSink

    registry = {"csv": CsvSink, "xlsx": ExcelSink, "excel": ExcelSink, "es": ElasticsearchSink}
    sinks = []
    for n in names:
        cls = registry.get(n.strip().lower())
        if cls is None:
            raise KeyError(f"unknown sink {n!r} (have: csv, xlsx, es)")
        sinks.append(cls())
    return sinks
