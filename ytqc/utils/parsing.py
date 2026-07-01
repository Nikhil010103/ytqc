"""Count/date text parsing — lifted verbatim from yt_qc_checker.py."""
from __future__ import annotations

import re


def parse_count(text: str) -> int:
    """Parse '1.23M', '456K', '1,234,567' → int.  Returns 0 on failure."""
    if not text:
        return 0
    text = re.sub(r"[,\s]", "", str(text))
    m = re.search(r"([\d.]+)([KMBkmb]?)", text)
    if not m:
        return 0
    num = float(m.group(1))
    suffix = m.group(2).upper()
    return int(num * {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix, 1))


def parse_date_to_days(text: str) -> float:
    """'2 weeks ago', '3 months ago' → approximate days.  Unknown → 365."""
    if not text:
        return 365.0
    m = re.search(r"(\d+)\s+(second|minute|hour|day|week|month|year)", text, re.I)
    if not m:
        return 365.0
    n, unit = int(m.group(1)), m.group(2).lower()
    factors = {
        "second": 1 / 86400, "minute": 1 / 1440, "hour": 1 / 24,
        "day": 1, "week": 7, "month": 30, "year": 365,
    }
    return n * factors.get(unit, 1)


def parse_timestamp_to_seconds(text: str) -> float:
    """'1:23' → 83.0, '1:02:03' → 3723.0.  Unknown → 0."""
    if not text:
        return 0.0
    parts = [p for p in re.split(r"[:.]", text.strip()) if p.isdigit()]
    if not parts:
        return 0.0
    secs = 0.0
    for p in parts:
        secs = secs * 60 + int(p)
    return secs
