"""The chat agent's tools — plain functions wrapping existing ytqc seams.

Each tool takes an injected AgentContext as its first arg (`ctx`, stripped from
the schema), returns a JSON-serializable dict, and NEVER raises out of the
registry (errors come back as {"error": ...} so the agent recovers
conversationally). Docstring first-paragraphs are written for the model — they
become the tool descriptions the LLM sees.
"""
from __future__ import annotations

import glob
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from rich.console import Console

from ytqc.config import YtqcConfig

log = logging.getLogger("ytqc.agent.tools")

_RUN_ID_RE = re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}")
_CONFIRM_THRESHOLD = 0  # 0 → never block (user chose "just run"); kept for future use


@dataclass
class AgentContext:
    cfg: YtqcConfig
    console: Console
    output_dir: str
    last_run_id: Optional[str] = None
    confirm: Callable[[str], bool] = field(default=lambda _msg: True)


def _resolve_path(raw: str) -> str:
    """Expand ~, try CWD-relative, then glob. Raises FileNotFoundError (caught
    by the tool → {"error"}) with a helpful message gemma passes literal '~/…'."""
    if not raw:
        raise FileNotFoundError("no path given")
    p = Path(raw).expanduser()
    if p.exists():
        return str(p)
    if not p.is_absolute():
        cand = Path.cwd() / p
        if cand.exists():
            return str(cand)
    matches = glob.glob(os.path.expanduser(raw))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise FileNotFoundError(f"{raw!r} matched {len(matches)} files — be more specific: {matches[:5]}")
    raise FileNotFoundError(f"no file at {raw!r} (tried ~ expansion, current dir, and glob)")


# Extensions that signal "this is a file, not a folder". Curated on purpose so
# legitimate dotted folder names (v1.2, my.project) are NOT flagged — only the
# last path component's suffix is checked, against this known set.
_FILE_EXTS = {
    ".csv", ".xlsx", ".xls", ".excel", ".tsv", ".json", ".parquet",
    ".txt", ".pdf", ".zip", ".gz", ".xml", ".html", ".doc", ".docx",
}


def _looks_like_file_path(raw: str) -> Optional[str]:
    """If `raw`'s final component ends in a known file extension, return that
    extension (a folder never has one); else None. Used to catch a user who
    typed a file name where an output FOLDER is expected."""
    if not raw or not raw.strip():
        return None
    ext = Path(raw.strip()).suffix.lower()
    return ext if ext in _FILE_EXTS else None


def _resolve_output_dir(raw: str) -> str:
    """Expand ~ and make a (possibly new) output FOLDER absolute, creating it if
    needed. The chat agent asks the user where to save before every run, so this
    path usually doesn't exist yet — unlike _resolve_path (an existing input
    file), we create it. Raises if the path exists but isn't a directory."""
    if not raw or not raw.strip():
        raise ValueError("no folder given")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    if p.exists() and not p.is_dir():
        raise NotADirectoryError(f"{p} exists but is not a folder")
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def _est_minutes(items: list) -> float:
    n_videos = sum(1 for i in items if i.type == "video")
    n_channels = len(items) - n_videos
    lanes = max(1, 4)  # rough; real lanes applied at run time
    return round((n_videos * 16 + n_channels * 50) / 60 / lanes, 1)


def _items_from(path: Optional[str], ids: Optional[str], item_type: Optional[str]):
    """Resolve a tool's path/ids args into (items, ParseReport|None).

    - path: CSV/Excel read (pandas); the id column is auto-detected and its
      cells normalized like pasted text. Returns a ParseReport too (which column
      was detected, what was deduped/unrecognized).
    - ids: pasted/free text → deterministic normalization (extract canonical
      YouTube ids/urls/@handles, dedupe, ignore noise) + a report the caller can
      surface ("found N unique, ignored M").
    """
    from ytqc.cli import _read_input
    from ytqc.input_parse import parse_items
    if path:
        items, report, _id_col = _read_input(_resolve_path(path), default_type=item_type)
        return items, report
    if ids:
        return parse_items(ids, default_type=item_type)
    return None, None


# ── tools ──────────────────────────────────────────────────────────────────

def run_qc(ctx: AgentContext, path: str = None, ids: str = None, item_type: str = None,
           output_dir: str = None, lanes: int = None, workers: int = None, provider: str = None,
           channel_pages: int = None, limit: int = None, no_comments: bool = False) -> dict:
    """Start a QC run over a CSV/Excel file (path) or a pasted list of channel/video ids. Pass the user's raw pasted text VERBATIM as `ids` — messy multi-column lines, URLs, and @handles are fine; the tool extracts and dedupes the canonical YouTube ids itself, so do not pre-split or re-type them. `output_dir` is the folder to save results into and is REQUIRED — always ask the user where to save first; the run will not start without it. This opens real browser tabs and takes minutes; it returns a summary when finished. Use lanes/workers to set parallelism.

    Returns the run id, item counts, where results were written (output_dir +
    results_path), and a `parsed` breakdown of how the input was understood.
    """
    from ytqc.cli import _apply_parallelism
    from ytqc.pipeline.orchestrator import Orchestrator
    from ytqc.pipeline.state import RunState
    from ytqc.sinks.base import build_sinks

    items, parse_report = _items_from(path, ids, item_type)
    if not items:
        return {"error": "give me a file path (e.g. ~/Desktop/channels.csv) or a list of ids to QC."}
    if limit:
        items = items[:limit]

    # Output location is required: do NOT start a run until the user has said
    # where to save. Bounce back an ask the assistant relays, rather than
    # silently defaulting to ./ytqc_runs.
    if not output_dir or not str(output_dir).strip():
        return {"need_output_dir": True,
                "ask": "Where should I save the results? Give me a folder path "
                       "(e.g. ~/Desktop/qc-results) and I'll start the run."}
    # A save location is a FOLDER, so it has no file extension. If the user gave
    # something that ends in .csv/.xlsx/etc., it's almost certainly a slip — don't
    # silently create a folder named "results.csv"; bounce back and confirm.
    ext = _looks_like_file_path(output_dir)
    if ext:
        parent = Path(output_dir).expanduser().parent
        return {"need_output_dir": True,
                "ask": f"'{output_dir}' looks like a file name, not a folder "
                       f"(folders don't have a {ext} extension). Results always save "
                       f"as <folder>/<run_id>/results.csv. Did you mean the folder "
                       f"{parent}/ , or a different one?"}
    try:
        resolved_out = _resolve_output_dir(output_dir)
    except Exception as exc:
        return {"error": f"couldn't use that output folder ({output_dir!r}): {exc}. "
                         "Give me a folder path I can write to."}

    cfg = ctx.cfg.model_copy(deep=True)
    cfg.output_dir = resolved_out
    # Chat default is 2 lanes (the measured sweet spot) when the user doesn't
    # specify; the assistant is also prompted to confirm the count before running.
    _apply_parallelism(cfg, lanes if lanes is not None else 2, workers)
    if channel_pages is not None:
        cfg.sampling.channel_pages = max(0, channel_pages)

    if _CONFIRM_THRESHOLD and len(items) > _CONFIRM_THRESHOLD:
        est = _est_minutes(items)
        if not ctx.confirm(f"Run QC on {len(items)} item(s) (~{est} min)? [y/N]"):
            return {"status": "cancelled", "items": len(items)}

    out_dir = cfg.output_dir
    state = RunState(out_dir)
    sinks = build_sinks(cfg.sinks)
    for s in sinks:
        s.open(state.run_id, out_dir)
    ctx.console.print(f"[dim]starting run [bold]{state.run_id}[/] — {len(items)} item(s); "
                      f"watch the progress bar, I'll summarize when it's done.[/]")
    try:
        orch = Orchestrator(cfg, items, sinks, state, provider=provider,
                            use_cache=True, comments=not no_comments, console=ctx.console)
        stats = orch.run()
    except Exception as exc:
        log.exception("run_qc failed")
        return {"error": f"the run failed: {exc}", "run_id": state.run_id}
    finally:
        for s in sinks:
            try:
                s.close()
            except Exception:
                pass
    # a fatal browser-setup failure (e.g. extension not connected) → report it
    # as an error so the assistant doesn't claim a "0 items" run succeeded.
    setup_err = getattr(orch, "_setup_error", None)
    if setup_err and stats.done == 0:
        return {"error": setup_err, "run_id": state.run_id, "items": 0}
    ctx.last_run_id = state.run_id
    tiers = dict(sorted(stats.tier_counts.items(), key=lambda kv: -kv[1])[:8])
    result = {
        "run_id": state.run_id, "items": stats.done, "errors": stats.errors,
        "unsafe": stats.unsafe, "needs_review": stats.needs_review,
        "tier_distribution": tiers,
        "output_dir": out_dir,
        "run_dir": str(Path(out_dir) / state.run_id),
        "results_path": str(Path(out_dir) / state.run_id / "results.csv"),
    }
    if parse_report is not None:
        result["parsed"] = parse_report.as_dict()
    return result


def inspect_input(ctx: AgentContext, path: str = None, ids: str = None,
                  item_type: str = None) -> dict:
    """Peek at QC input — a CSV/Excel file (path) OR a pasted list of ids/urls/@handles (ids) — and report how many UNIQUE channels and videos it contains, plus anything it ignored or deduped, WITHOUT running QC. Pass pasted text verbatim as `ids`. Use this to confirm a messy paste before a long run.

    Returns counts, a few sample ids, and what was unrecognized/deduped.
    """
    try:
        items, report = _items_from(path, ids, item_type)
    except FileNotFoundError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": f"couldn't read input: {exc}"}
    if not items:
        return {"error": "give me a file path or a list of ids to inspect."}
    n_videos = sum(1 for i in items if i.type == "video")
    out = {
        "total": len(items),
        "channels": len(items) - n_videos, "videos": n_videos,
        "sample_ids": [i.id for i in items[:5]],
        "est_minutes": _est_minutes(items),
    }
    if path:
        out["path"] = _resolve_path(path)
    if report is not None:
        out["deduped"] = report.n_deduped
        out["unrecognized"] = report.unrecognized
        if report.detected_column is not None:
            out["detected_column"] = report.detected_column
    return out


def list_runs(ctx: AgentContext) -> dict:
    """List previous QC runs in the output directory, newest first, with item counts and when they ran.

    Returns recent runs.
    """
    import pandas as pd
    root = Path(ctx.output_dir)
    if not root.exists():
        return {"runs": [], "count": 0}
    runs = []
    for d in root.iterdir():
        if not (d.is_dir() and _RUN_ID_RE.fullmatch(d.name)):
            continue
        csv = d / "results.csv"
        items = unsafe = 0
        if csv.exists():
            try:
                df = pd.read_csv(csv, dtype=str, keep_default_na=False)
                items = len(df)
                unsafe = int((df.get("brand_safety_is_safe", pd.Series([], dtype=str))
                              .astype(str).str.lower() == "false").sum())
            except Exception:
                pass
        runs.append({"run_id": d.name, "items": items, "unsafe": unsafe,
                     "when": time.strftime("%Y-%m-%d %H:%M",
                                           time.localtime(d.stat().st_mtime))})
    runs.sort(key=lambda r: r["run_id"], reverse=True)
    return {"runs": runs[:15], "count": len(runs)}


def show_results(ctx: AgentContext, run_id: str = None, only: str = None) -> dict:
    """Show the results of a QC run — overall tier distribution, unsafe count, needs-review count — and optionally filter to a subset. `only` can be 'unsafe', 'needs_review', 'errors', or a tier_1 category name. Defaults to the most recent run.

    Returns a summary plus up to 15 matching rows.
    """
    import pandas as pd
    rid = run_id or ctx.last_run_id
    root = Path(ctx.output_dir)
    if not rid:
        runs = [d.name for d in root.iterdir()
                if d.is_dir() and _RUN_ID_RE.fullmatch(d.name)] if root.exists() else []
        if not runs:
            return {"error": "no runs found yet — start one with run_qc first."}
        rid = sorted(runs, reverse=True)[0]
    csv = root / rid / "results.csv"
    if not csv.exists():
        return {"error": f"no results.csv for run {rid!r}."}
    try:
        df = pd.read_csv(csv, dtype=str, keep_default_na=False)
    except Exception as exc:
        return {"error": f"couldn't read results for {rid!r}: {exc}"}

    def _low(col):
        return df.get(col, pd.Series([""] * len(df))).astype(str).str.lower()

    unsafe = (_low("brand_safety_is_safe") == "false")
    review = _low("needs_review").isin(["true", "1"])
    errors = (_low("status") == "error")
    matched = df
    if only:
        o = only.strip().lower()
        if o in ("unsafe", "not_safe", "brand_unsafe"):
            matched = df[unsafe]
        elif o in ("needs_review", "review", "needs review"):
            matched = df[review]
        elif o in ("error", "errors", "failed"):
            matched = df[errors]
        else:  # treat as a tier_1 filter
            matched = df[_low("tier_1") == o]
    cols = [c for c in ("id", "name", "tier_1", "brand_safety_is_safe",
                        "brand_safety_risk_level", "needs_review", "confidence") if c in df.columns]
    rows = matched[cols].head(15).to_dict("records")
    tiers = (df["tier_1"].value_counts().head(10).to_dict()
             if "tier_1" in df.columns else {})
    return {
        "run_id": rid, "total": len(df),
        "tier_distribution": tiers,
        "unsafe": int(unsafe.sum()), "needs_review": int(review.sum()),
        "errors": int(errors.sum()),
        "matched": len(matched), "rows": rows,
        "truncated": len(matched) > 15,
    }


def resume_run(ctx: AgentContext, run_id: str, path: str) -> dict:
    """Resume an interrupted QC run by its run id, reusing the original input file. Already-finished items are skipped.

    Returns a summary like run_qc.
    """
    from ytqc.cli import _read_input
    from ytqc.pipeline.orchestrator import Orchestrator
    from ytqc.pipeline.state import RunState
    from ytqc.sinks.base import build_sinks
    out_dir = ctx.cfg.output_dir
    try:
        state = RunState.resume(out_dir, run_id)
    except FileNotFoundError:
        return {"error": f"no run {run_id!r} to resume under {out_dir}."}
    try:
        items = _read_input(_resolve_path(path))
    except FileNotFoundError as exc:
        return {"error": str(exc)}
    sinks = build_sinks(ctx.cfg.sinks)
    for s in sinks:
        s.open(run_id, out_dir)
    try:
        stats = Orchestrator(ctx.cfg, items, sinks, state, console=ctx.console).run()
    except Exception as exc:
        return {"error": f"resume failed: {exc}", "run_id": run_id}
    finally:
        for s in sinks:
            try:
                s.close()
            except Exception:
                pass
    ctx.last_run_id = run_id
    return {"run_id": run_id, "items": stats.done, "errors": stats.errors,
            "unsafe": stats.unsafe, "needs_review": stats.needs_review}


def check_setup(ctx: AgentContext, provider: str = None) -> dict:
    """Check that the browser bridge (kimi-webbridge) and the AI model endpoint are reachable — run this if QC isn't working or before a big batch.

    Returns whether everything is connected.
    """
    from ytqc.cli import _doctor_checks
    try:
        ok = _doctor_checks(ctx.cfg, provider, ctx.console)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": bool(ok), "checked": ["kimi-webbridge (browser)", "LLM endpoint"]}


def show_taxonomy(ctx: AgentContext) -> dict:
    """Show the QC vocabularies: the closed list of tier-1 content categories, the kids age groups, and the brand-safety categories.

    Returns the category lists.
    """
    from ytqc.taxonomy import KIDS_AGE_GROUPS, SAFETY_CATEGORIES, TIER_1_CATEGORIES
    return {
        "tier_1": sorted(TIER_1_CATEGORIES),
        "kids_age_groups": list(KIDS_AGE_GROUPS),
        "safety_categories": list(SAFETY_CATEGORIES),
    }


ALL_TOOLS: list[Callable] = [
    run_qc, inspect_input, list_runs, show_results, resume_run, check_setup, show_taxonomy,
]


class ToolRegistry:
    def __init__(self, ctx: AgentContext):
        self.ctx = ctx
        self._fns: dict[str, Callable] = {f.__name__: f for f in ALL_TOOLS}

    def schemas(self) -> list[dict]:
        from ytqc.agent.schema import build_tool_schema
        return [build_tool_schema(f) for f in ALL_TOOLS]

    def names(self) -> list[str]:
        return list(self._fns)

    def dispatch(self, name: str, raw_args: dict) -> dict:
        from ytqc.agent.schema import sanitize_kwargs
        fn = self._fns.get(name)
        if fn is None:
            return {"error": f"unknown tool {name!r}; available: {self.names()}"}
        kwargs, dropped = sanitize_kwargs(fn, raw_args or {})
        if dropped:
            log.info("dropped unknown args for %s: %s", name, dropped)
        try:
            result = fn(self.ctx, **kwargs)
            return result if isinstance(result, dict) else {"result": result}
        except Exception as exc:
            log.exception("tool %s raised", name)
            return {"error": f"{type(exc).__name__} in {name}: {exc}"}
