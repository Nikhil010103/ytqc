"""Elasticsearch sink — stub for the post-POC phase.

Target doc shape mirrors yt_llm's channel_classification index:
{id, type, tier_1, tier_2, keywords, topics, brand_safety: {...},
 language, targeted_region, audience: {...}, stats: {...}, qc: {...}}.
"""
from __future__ import annotations

from ytqc.models import QCRecord
from ytqc.sinks.base import ResultSink


class ElasticsearchSink(ResultSink):
    def open(self, run_id: str, output_dir: str) -> None:
        raise NotImplementedError(
            "ElasticsearchSink is a stub. Configure an ES endpoint and implement "
            "bulk upsert into a channel_classification-compatible index "
            "(see yt_llm's index mapping). POC uses csv/xlsx sinks."
        )

    def write(self, record: QCRecord) -> None:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover
        pass
