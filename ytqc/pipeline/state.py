"""Run state: JSONL checkpoint + artifacts dir. Resume = skip SUNK items,
re-enter others at their last completed stage using saved extraction artifacts.

Each run also writes a manifest.json describing the work it was given (input
file + a fingerprint of the item ids + the item count). `find_resumable` looks
that up so a re-run of the SAME list continues the unfinished run instead of
starting a second one from scratch — the auto-resume the CLI and the chat agent
both go through."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

log = logging.getLogger("ytqc.state")

MANIFEST = "manifest.json"


def fingerprint_items(items) -> str:
    """Stable identity of a work list: sha1 over its sorted unique 'type:id's.

    Deliberately independent of the file's name, path, row order and non-id
    columns — the same channels re-submitted from a copied/renamed sheet resume
    the same run, while adding or removing ids makes it a different run."""
    keys = sorted({f"{getattr(i, 'type', 'channel')}:{i.id}" for i in items})
    return hashlib.sha1("\n".join(keys).encode("utf-8")).hexdigest()[:16]


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

    def done_count(self) -> int:
        with self._lock:
            return sum(1 for stage in self._stages.values() if stage == "SUNK")

    # ── manifest / auto-resume ───────────────────────────────────────────
    def write_manifest(self, *, fingerprint: str, total_items: int,
                       input_path: Optional[str] = None, items=None) -> None:
        """Record what this run was asked to do, so a later invocation with the
        same list can find and continue it. Rewritten on resume (cheap, and it
        keeps `updated_at` meaningful).

        `items` stores the work list itself. It's what makes a run recoverable
        on its own: a run started from PASTED ids has no input file to re-read,
        so without this the unprocessed ids exist nowhere on disk (state.jsonl
        only records items that were finished) and the remainder is lost if the
        chat context goes. A few thousand ids is well under a megabyte."""
        payload = {
            "run_id": self.run_id,
            "fingerprint": fingerprint,
            "total_items": total_items,
            "input_path": input_path or "",
            "updated_at": time.time(),
        }
        if items is not None:
            payload["items"] = [{"id": i.id, "type": i.type} for i in items]
        prior = self.read_manifest() or {}
        payload["created_at"] = prior.get("created_at", payload["updated_at"])
        if "items" not in payload and prior.get("items"):
            payload["items"] = prior["items"]   # never drop a stored list
        tmp = self.root / (MANIFEST + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        tmp.replace(self.root / MANIFEST)       # atomic: never a half-written manifest

    def manifest_items(self) -> list:
        """The run's stored work list as InputItems, or [] if none was stored."""
        from ytqc.models import InputItem
        man = self.read_manifest() or {}
        out = []
        for raw in man.get("items") or []:
            try:
                out.append(InputItem(id=raw["id"], type=raw.get("type", "channel")))
            except Exception:
                continue
        return out

    def pending_items(self) -> list:
        """Stored work list minus everything already finished — what a resume
        still has to do. Empty when no list was stored."""
        return [i for i in self.manifest_items() if not self.is_done(i.id)]

    def read_manifest(self) -> Optional[dict]:
        p = self.root / MANIFEST
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    @classmethod
    def _matching_runs(cls, output_dir: str, fingerprint: str) -> list[tuple[float, str, bool]]:
        """(updated_at, run_id, finished) for every run given this exact work
        list. Unreadable/foreign run dirs are skipped — a bad manifest must
        never block a fresh run."""
        root = Path(output_dir)
        if not root.is_dir():
            return []
        out: list[tuple[float, str, bool]] = []
        for d in root.iterdir():
            if not d.is_dir():
                continue
            try:
                man = json.loads((d / MANIFEST).read_text())
            except (json.JSONDecodeError, OSError):
                continue
            if man.get("fingerprint") != fingerprint:
                continue
            try:
                done = cls(output_dir, run_id=d.name).done_count()
            except OSError:
                continue
            finished = done >= int(man.get("total_items") or 0)
            out.append((float(man.get("updated_at") or 0), d.name, finished))
        return out

    @classmethod
    def find_resumable(cls, output_dir: str, fingerprint: str) -> Optional[str]:
        """Newest run id under `output_dir` that was given this exact work list
        and hasn't finished it, or None."""
        unfinished = [(ts, rid) for ts, rid, done in
                      cls._matching_runs(output_dir, fingerprint) if not done]
        return max(unfinished)[1] if unfinished else None

    @classmethod
    def find_completed(cls, output_dir: str, fingerprint: str) -> Optional[str]:
        """Newest run that already FINISHED this exact list, or None.

        Re-running a completed list is legitimate (re-QC with fresh data), so
        this never blocks — it only lets the caller say so out loud, since
        silently redoing a few thousand channels is expensive if it was a slip."""
        finished = [(ts, rid) for ts, rid, done in
                    cls._matching_runs(output_dir, fingerprint) if done]
        return max(finished)[1] if finished else None
