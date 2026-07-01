"""Accuracy harness: join predictions ↔ gold on id, report per-field metrics.
POC acceptance gates: tier_1 ≥80%, language ≥95%, is_safe ≥90%, region ≥85%."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

GATES = {"tier_1": 0.80, "language": 0.95, "brand_safety_is_safe": 0.90, "targeted_region": 0.85}

# Default fields evaluated when --fields is not supplied. The first five are the
# original tuple; kids_age_group + is_premium_luxury were silently skipped before
# even though they are part of the schema/output.
DEFAULT_FIELDS = (
    "tier_1", "tier_2", "language", "targeted_region", "brand_safety_is_safe",
    "kids_age_group", "is_premium_luxury",
)


def _load(path: str) -> pd.DataFrame:
    p = Path(path)
    df = pd.read_excel(p, dtype=str) if p.suffix.lower() in (".xlsx", ".xls") else pd.read_csv(p, dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]
    return df


def _norm(s) -> str:
    return str(s).strip().lower() if pd.notna(s) else ""


def report(pred_path: str, gold_path: str, console: Console,
           fields: str | None = None) -> dict:
    pred = _load(pred_path)
    gold = _load(gold_path)
    merged = gold.merge(pred, on="id", suffixes=("_gold", "_pred"))
    if merged.empty:
        console.print("[red]no overlapping ids between pred and gold[/]")
        return {}

    if fields is None:
        eval_fields = DEFAULT_FIELDS
    elif fields.strip().lower() == "auto":
        # intersect the raw (pre-suffix) column names shared by gold & pred, minus id
        eval_fields = tuple(
            c for c in gold.columns if c != "id" and c in set(pred.columns)
        )
    else:
        eval_fields = tuple(f.strip() for f in fields.split(",") if f.strip())

    results: dict[str, float] = {}
    table = Table(title=f"accuracy vs gold ({len(merged)} matched items)")
    table.add_column("field"); table.add_column("accuracy"); table.add_column("gate"); table.add_column("pass")

    for field in eval_fields:
        g, p = f"{field}_gold", f"{field}_pred"
        if g not in merged.columns or p not in merged.columns:
            continue
        sub = merged[[g, p]].dropna()
        if sub.empty:
            continue
        if field == "tier_2":
            # fuzzy: token overlap counts as a match
            def tok_match(row):
                a, b = set(_norm(row[g]).split()), set(_norm(row[p]).split())
                return bool(a & b)
            acc = sub.apply(tok_match, axis=1).mean()
        else:
            acc = (sub[g].map(_norm) == sub[p].map(_norm)).mean()
        results[field] = float(acc)
        gate = GATES.get(field)
        verdict = "" if gate is None else ("[green]✓[/]" if acc >= gate else "[red]✗[/]")
        table.add_row(field, f"{acc:.1%}", f"≥{gate:.0%}" if gate else "—", verdict)

    # keyword overlap@5
    if "keywords_gold" in merged.columns and "keywords_pred" in merged.columns:
        def overlap(row):
            a = set(_norm(row["keywords_gold"]).replace(";", ",").split(","))
            b = set(_norm(row["keywords_pred"]).replace(";", ",").split(","))
            a = {x.strip() for x in a if x.strip()}
            b = {x.strip() for x in b if x.strip()}
            return len(a & b) / max(min(len(a), 5), 1)
        results["keyword_overlap@5"] = float(merged.apply(overlap, axis=1).mean())
        table.add_row("keyword_overlap@5", f"{results['keyword_overlap@5']:.1%}", "—", "")

    console.print(table)
    out = Path(pred_path).parent / "accuracy_report.csv"
    pd.DataFrame([results]).to_csv(out, index=False)
    console.print(f"[dim]wrote {out}[/]")
    return results
