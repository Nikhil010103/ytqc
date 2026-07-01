"""Sqlite response cache — kills repeat LLM spend across runs (mirrors' MongoDB
cache pattern, localized). Keyed by sha256(provider+model+PROMPT_VERSION+inputs)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from ytqc import PROMPT_VERSION
from ytqc.config import CONFIG_DIR


class ResponseCache:
    def __init__(self, path: Optional[Path] = None, ttl_days: int = 7, enabled: bool = True):
        self.enabled = enabled
        self.ttl_s = ttl_days * 86400
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()
        if not enabled:
            self._db = None
            return
        path = path or CONFIG_DIR / "cache.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY, response TEXT, created_at REAL)"
        )
        self._db.commit()

    @staticmethod
    def make_key(provider: str, model: str, system: str, user: str, images: list[str] | None) -> str:
        h = hashlib.sha256()
        h.update(f"{provider}|{model}|{PROMPT_VERSION}".encode())
        h.update(system.encode())
        h.update(user.encode())
        for img in images or []:
            h.update(hashlib.sha256(img.encode()).digest())
        return h.hexdigest()

    def get(self, key: str) -> Optional[dict]:
        if not self._db:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT response, created_at FROM responses WHERE key = ?", (key,)
            ).fetchone()
            if not row:
                self.misses += 1
                return None
            response, created_at = row
            if time.time() - created_at > self.ttl_s:
                self._db.execute("DELETE FROM responses WHERE key = ?", (key,))
                self._db.commit()
                self.misses += 1
                return None
            self.hits += 1
            return json.loads(response)

    def put(self, key: str, value: dict) -> None:
        if not self._db:
            return
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO responses (key, response, created_at) VALUES (?, ?, ?)",
                (key, json.dumps(value), time.time()),
            )
            self._db.commit()
