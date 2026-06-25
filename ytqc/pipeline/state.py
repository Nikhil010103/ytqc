"""Run state: JSONL checkpoint + artifacts dir. Resume = skip SUNK items,
re-enter others at their last completed stage using saved extraction artifacts."""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Optional


class RunState:
    def __init__(self, output_dir: str, run_id: Optional[str] = None):
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.root = Path(output_dir) / self.run_id
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.jsonl"
        self._lock = threading.Lock()
        self._stages: dict[str, str] = {}
        if self.state_path.exists():
            for line in self.state_path.read_text().splitlines():
                try:
                    rec = json.loads(line)
                    self._stages[rec["item_id"]] = rec["stage"]
                except (json.JSONDecodeError, KeyError):
                    continue

    @classmethod
    def resume(cls, output_dir: str, run_id: str) -> "RunState":
        root = Path(output_dir) / run_id
        if not root.exists():
            raise FileNotFoundError(f"run {run_id!r} not found under {output_dir}")
        return cls(output_dir, run_id=run_id)

    def stage_of(self, item_id: str) -> Optional[str]:
        with self._lock:
            return self._stages.get(item_id)

    def mark(self, item_id: str, stage: str, payload: Optional[dict] = None,
             error: Optional[str] = None) -> None:
        rec = {"item_id": item_id, "stage": stage, "ts": time.time()}
        if error:
            rec["error"] = error[:500]
        with self._lock:
            with self.state_path.open("a") as f:
                f.write(json.dumps(rec) + "\n")
            self._stages[item_id] = stage
        if payload is not None:
            self.save_artifact(item_id, f"{stage.lower()}.json", payload)

    def save_artifact(self, item_id: str, name: str, payload: dict) -> None:
        d = self.artifacts / item_id.replace("/", "_")
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(json.dumps(payload, ensure_ascii=False, default=str))

    def load_artifact(self, item_id: str, name: str) -> Optional[dict]:
        p = self.artifacts / item_id.replace("/", "_") / name
        if p.exists():
            try:
                return json.loads(p.read_text())
            except json.JSONDecodeError:
                return None
        return None

    def is_done(self, item_id: str) -> bool:
        with self._lock:
            return self._stages.get(item_id) == "SUNK"
