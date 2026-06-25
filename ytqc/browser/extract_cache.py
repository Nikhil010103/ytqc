"""Cross-run extraction cache — skips re-scraping a channel/video that was
extracted recently. Mirrors the LLM ResponseCache (ytqc/llm/cache.py): SQLite,
thread-safe, TTL delete-on-read. Stores the FULL serialized VideoExtract/
ChannelExtract, so a hit is byte-identical to a fresh extraction (no field or
context is lost). Keyed by sha256(item_id | item_type | EXTRACT_SCHEMA_VERSION),
so a model-shape change auto-invalidates stale rows.

Channels carry fast-moving signal (subs, velocity, recent uploads), so they get
a much shorter TTL than videos by default.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from ytqc import EXTRACT_SCHEMA_VERSION
from ytqc.config import CONFIG_DIR


class ExtractCache:
    def __init__(self, path: Optional[Path] = None, video_ttl_days: int = 14,
                 channel_ttl_days: int = 3, enabled: bool = True):
        self.enabled = enabled
        self._ttl_s = {"video": video_ttl_days * 86400, "channel": channel_ttl_days * 86400}
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()
        if not enabled:
            self._db = None
            return
        path = path or CONFIG_DIR / "extract_cache.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS extracts "
            "(key TEXT PRIMARY KEY, kind TEXT, payload TEXT, created_at REAL)"
        )
        self._db.commit()

    @staticmethod
    def make_key(item_id: str, item_type: str) -> str:
        h = hashlib.sha256()
        h.update(f"{item_id}|{item_type}|{EXTRACT_SCHEMA_VERSION}".encode())
        return h.hexdigest()

    def _ttl_for(self, kind: str) -> int:
        return self._ttl_s.get(kind, self._ttl_s["video"])

    def get(self, key: str, kind: str) -> Optional[dict]:
        if not self._db:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT payload, created_at FROM extracts WHERE key = ?", (key,)
            ).fetchone()
            if not row:
                self.misses += 1
                return None
            payload, created_at = row
            if time.time() - created_at > self._ttl_for(kind):
                self._db.execute("DELETE FROM extracts WHERE key = ?", (key,))
                self._db.commit()
                self.misses += 1
                return None
            self.hits += 1
            return json.loads(payload)

    def put(self, key: str, kind: str, payload: dict) -> None:
        if not self._db:
            return
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO extracts (key, kind, payload, created_at) "
                "VALUES (?, ?, ?, ?)",
                (key, kind, json.dumps(payload, default=str), time.time()),
            )
            self._db.commit()
