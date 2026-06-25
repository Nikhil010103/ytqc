"""ytqc — browser-driven agentic YouTube QC CLI."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from ytqc import __version__
from ytqc.config import CONFIG_PATH, DEFAULT_CONFIG, load_config, save_config

app = typer.Typer(help="ytqc — automated YouTube channel/video QC for adtech",
                  no_args_is_help=False, add_completion=False)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"ytqc v{__version__}")
        raise typer.Exit(0)


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Show the ytqc version and exit"),
):
    """ytqc — automated YouTube channel/video QC for adtech.

    Run with no command to open the interactive chat assistant."""
    if ctx.invoked_subcommand is None:
        # bare `ytqc` → conversational chat (lazy import so subcommands stay fast)
        from ytqc.agent import run_chat
        run_chat()
        raise typer.Exit(0)


@app.command()
def chat(
    provider: Optional[str] = typer.Option(None, help="Provider profile for the chat brain"),
    model: Optional[str] = typer.Option(None, help="Model for the chat brain"),
):
    """Open the interactive ytqc chat assistant."""
    from ytqc.agent import run_chat
    run_chat(provider=provider, model=model)


@app.command()
def version():
    """Print the ytqc version and exit."""
    console.print(f"ytqc v{__version__}")
    raise typer.Exit(0)


def _resolve_provider(cfg, provider: Optional[str]):
    """Look up a provider profile, surfacing config's KeyError as a clean exit."""
    try:
        return cfg.provider(provider)
    except KeyError as exc:
        # config.provider() raises KeyError with a helpful message (available names);
        # use .args[0] so repr-quoting from str(KeyError) doesn't leak into the output
        msg = exc.args[0] if exc.args else str(exc)
        console.print(f"[red]error:[/] {msg}")
        raise typer.Exit(1)


_LANE_CEILING = 20


def _apply_parallelism(cfg, lanes: Optional[int], workers: Optional[int]) -> None:
    """Apply --lanes/--workers overrides, clamp to the safe ceiling, and warn
    when concurrency is high enough to need a dedicated account."""
    if lanes is not None:
        if lanes > _LANE_CEILING:
            console.print(f"[yellow]warning:[/] --lanes {lanes} exceeds the {_LANE_CEILING} "
                          f"ceiling; clamping to {_LANE_CEILING}.")
            lanes = _LANE_CEILING
        cfg.pipeline.browser_lanes = max(1, lanes)
    if workers is not None:
        cfg.pipeline.analysis_workers = max(1, workers)
    if cfg.pipeline.browser_lanes > 6:
        console.print(
            f"[yellow]⚠ {cfg.pipeline.browser_lanes} parallel browser tabs[/] — this is a strong "
            "bot-detection signal from one account/IP. Use a dedicated (ideally Premium) "
            "Google account; expect occasional captcha halts (resume continues).")


def _read_input(path: str) -> list:
    import pandas as pd

    from ytqc.models import InputItem
    p = Path(path)
    df = (pd.read_excel(p, dtype=str) if p.suffix.lower() in (".xlsx", ".xls")
          else pd.read_csv(p, dtype=str))
    df.columns = [c.strip().lower() for c in df.columns]
    if "id" not in df.columns:
        raise typer.BadParameter("input file must have an 'id' column")
    if "type" not in df.columns:
        df["type"] = "channel"
    items = []
    for row in df.dropna(subset=["id"]).to_dict("records"):
        items.append(InputItem(
            id=str(row["id"]).strip(),
            type=str(row.get("type", "channel")).strip().lower(),
            label=(str(row["label"]).strip() if row.get("label") else None),
        ))
    return items


@app.command()
def run(
    input: str = typer.Option(..., "--input", "-i", help="CSV/Excel with id,type columns"),
    provider: Optional[str] = typer.Option(None, help="Provider profile name from config"),
    model: Optional[str] = typer.Option(None, help="Override the profile's model"),
    sink: str = typer.Option("csv,xlsx", help="Comma-separated sinks: csv,xlsx,es"),
    channel_pages: Optional[int] = typer.Option(None, "--channel-pages", help="Continuation pages of titles to scrape per channel (~30 each; default 4)"),
    lanes: Optional[int] = typer.Option(None, "--lanes", help="Parallel browser tabs (default 4, max 20; ~2 is the measured sweet spot — see docs)"),
    workers: Optional[int] = typer.Option(None, "--workers", help="Parallel LLM analysis workers (default 5)"),
    limit: Optional[int] = typer.Option(None, help="Process only the first N items"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan + connectivity check only"),
    extract_only: bool = typer.Option(False, "--extract-only", help="Stop after browser extraction"),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the LLM response cache"),
    no_extract_cache: bool = typer.Option(False, "--no-extract-cache",
                                          help="Bypass the cross-run extraction cache (always re-scrape)"),
    no_comments: bool = typer.Option(False, "--no-comments", help="Skip comment scraping (-6s/video)"),
    no_vidiq: bool = typer.Option(False, "--no-vidiq", help="Disable VidIQ overlay scraping (requires the VidIQ Chrome extension)"),
    output_dir: Optional[str] = typer.Option(None, help="Run output directory"),
    verbose: bool = typer.Option(False, "-v", help="Debug logging"),
):
    """QC every channel/video in the input file."""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    cfg = load_config()
    if channel_pages is not None:
        cfg.sampling.channel_pages = max(0, channel_pages)
    if no_vidiq:
        cfg.pipeline.vidiq_scrape = False
    _apply_parallelism(cfg, lanes, workers)
    out_dir = output_dir or cfg.output_dir

    items = _read_input(input)
    if limit:
        items = items[:limit]
    console.print(f"[bold]ytqc v{__version__}[/] — {len(items)} item(s) from {input}")

    profile = _resolve_provider(cfg, provider)
    eff_model = model or profile.model
    console.print(f"provider: [cyan]{provider or cfg.active_provider}[/] "
                  f"model: [cyan]{eff_model}[/] sinks: {sink}")

    n_videos = sum(1 for i in items if i.type == "video")
    n_channels = len(items) - n_videos
    est_calls = n_videos * 2 + n_channels * 2     # channel: vision + synthesizer (+judge sometimes)
    lanes_n = cfg.pipeline.browser_lanes
    par_browser_min = (n_videos * 16 + n_channels * 50) / 60 / max(lanes_n, 1)
    console.print(f"plan: {n_videos} videos, {n_channels} channels "
                  f"→ ~{est_calls} LLM calls | {lanes_n} lanes / "
                  f"{cfg.pipeline.analysis_workers} workers → ~{par_browser_min:.0f} min browser time")

    if dry_run:
        _doctor_checks(cfg, provider, console)
        console.print("[yellow]dry-run — nothing executed[/]")
        raise typer.Exit(0)

    from ytqc.pipeline.orchestrator import Orchestrator
    from ytqc.pipeline.state import RunState
    from ytqc.sinks.base import build_sinks

    state = RunState(out_dir)
    sinks = build_sinks(sink.split(","))
    for s in sinks:
        s.open(state.run_id, out_dir)
    console.print(f"run id: [bold]{state.run_id}[/] → {Path(out_dir) / state.run_id}")

    orch = Orchestrator(cfg, items, sinks, state, provider=provider, model=model,
                        use_cache=not no_cache, use_extract_cache=not no_extract_cache,
                        comments=not no_comments,
                        extract_only=extract_only, console=console)
    interrupted = False
    try:
        orch.run()
    except KeyboardInterrupt:
        # orchestrator already closed the browser tabs + printed the resume hint
        interrupted = True
    finally:
        for s in sinks:
            s.close()
    console.print(f"[green]results:[/] {Path(out_dir) / state.run_id}")
    if interrupted:
        # An interrupted run can leave non-daemon brief-pool threads blocked on
        # Ollama; Python's atexit join would hang on them (a stray Ctrl-C then
        # prints an ugly trace). Tabs are closed + results checkpointed already,
        # so hard-exit past that join with the conventional SIGINT code.
        try:
            sys.stdout.flush()
        except Exception:
            pass
        os._exit(130)


@app.command()
def resume(
    run_id: str = typer.Argument(..., help="Run id to resume"),
    input: str = typer.Option(..., "--input", "-i", help="The original input file"),
    provider: Optional[str] = typer.Option(None),
    sink: str = typer.Option("csv,xlsx"),
    lanes: Optional[int] = typer.Option(None, "--lanes", help="Parallel browser tabs"),
    workers: Optional[int] = typer.Option(None, "--workers", help="Parallel LLM workers"),
    output_dir: Optional[str] = typer.Option(None),
):
    """Resume an interrupted run — completed items are skipped, extracted-but-
    unanalyzed items reuse their saved browser artifacts."""
    cfg = load_config()
    _resolve_provider(cfg, provider)  # fail cleanly on a bad --provider before resuming
    _apply_parallelism(cfg, lanes, workers)
    out_dir = output_dir or cfg.output_dir
    from ytqc.pipeline.orchestrator import Orchestrator
    from ytqc.pipeline.state import RunState
    from ytqc.sinks.base import build_sinks

    state = RunState.resume(out_dir, run_id)
    items = _read_input(input)
    sinks = build_sinks(sink.split(","))
    for s in sinks:
        s.open(run_id, out_dir)
    orch = Orchestrator(cfg, items, sinks, state, provider=provider, console=console)
    try:
        orch.run()
    finally:
        for s in sinks:
            s.close()


@app.command()
def configure():
    """Write the default config to ~/.ytqc/config.yaml for editing."""
    if CONFIG_PATH.exists():
        console.print(f"config already exists: {CONFIG_PATH}")
    else:
        save_config(DEFAULT_CONFIG)
        console.print(f"[green]wrote default config:[/] {CONFIG_PATH}")
    cfg = load_config()
    table = Table(title="provider profiles")
    table.add_column("name"); table.add_column("base_url"); table.add_column("model")
    table.add_column("vision"); table.add_column("active")
    for name, p in cfg.providers.items():
        table.add_row(name, p.base_url, p.model, str(p.supports_vision),
                      "✓" if name == cfg.active_provider else "")
    console.print(table)


@app.command()
def taxonomy():
    """Show the closed vocabularies the QC record uses."""
    from ytqc.taxonomy import (KIDS_AGE_GROUPS, SAFETY_CATEGORIES,
                               TIER_1_CATEGORIES)
    console.print(f"[bold]tier_1 ({len(TIER_1_CATEGORIES)} values):[/]")
    for c in sorted(TIER_1_CATEGORIES):
        console.print(f"  • {c}")
    console.print(f"\n[bold]kids age groups:[/] {', '.join(KIDS_AGE_GROUPS)}")
    console.print(f"[bold]brand-safety categories:[/] {', '.join(SAFETY_CATEGORIES)}")


def _doctor_checks(cfg, provider: Optional[str], console: Console) -> bool:
    """Probe kimi-webbridge + the LLM endpoint and print the results. Thin wrapper
    over ytqc.setup.checks so `doctor`, the chat `/setup` check, and the setup
    wizard all share the same probes."""
    from ytqc.setup import checks
    return checks.render_doctor(checks.doctor_probes(cfg, provider), console)


@app.command()
def doctor(provider: Optional[str] = typer.Option(None)):
    """Check kimi-webbridge + LLM endpoint reachability and config validity."""
    cfg = load_config()
    ok = _doctor_checks(cfg, provider, console)
    console.print("[green]all checks passed[/]" if ok else "[red]issues found[/]")
    raise typer.Exit(0 if ok else 1)


@app.command()
def setup(
    provider: Optional[str] = typer.Option(None, help="Provider profile to set up (default: active)"),
    model: Optional[str] = typer.Option(None, help="Override the model to install"),
    non_interactive: bool = typer.Option(False, "--non-interactive",
                                         help="Don't prompt; surface manual steps and exit"),
):
    """One-command setup: installs/starts Ollama + the model, the kimi-webbridge
    daemon, force-installs the Chrome extensions, then opens the chat assistant."""
    from ytqc.setup.wizard import run_setup
    ok = run_setup(provider=provider, model=model, non_interactive=non_interactive)
    raise typer.Exit(0 if ok else 1)


@app.command()
def start(
    provider: Optional[str] = typer.Option(None, help="Provider profile for the chat brain"),
    model: Optional[str] = typer.Option(None, help="Model for the chat brain"),
):
    """Boot the local services (Ollama, kimi-webbridge, Chrome) then open chat.
    This is what the desktop launcher runs."""
    from ytqc.agent import run_chat
    from ytqc.setup.wizard import boot_services
    boot_services(console)
    run_chat(provider=provider, model=model)


@app.command()
def guide():
    """Show the setup guide — prerequisites, the one-command flow, and the three
    manual steps (YouTube sign-in, ollama signin, Chrome restart)."""
    from ytqc.setup.guide import render_guide
    render_guide(console)


@app.command(name="install-launcher")
def install_launcher():
    """Create a double-click desktop launcher that runs `ytqc start`."""
    from ytqc.setup.wizard import install_launcher as _install
    res = _install(console)
    color = "green" if res.status == "ok" else "red"
    console.print(f"[{color}]{res.message}[/]")
    if res.hint:
        console.print(f"[dim]{res.hint}[/]")


@app.command()
def accuracy(
    pred: str = typer.Option(..., help="results.csv from a run"),
    gold: str = typer.Option(..., help="gold labels file (xlsx/csv) with id + expected fields"),
    fields: Optional[str] = typer.Option(
        None, "--fields",
        help="Comma-separated fields to evaluate; 'auto' = intersect gold & pred "
             "columns minus 'id'. Default: standard fields plus kids_age_group, "
             "is_premium_luxury"),
):
    """Compare a run's predictions against QC-team gold labels."""
    from ytqc.harness.accuracy import report
    report(pred, gold, console, fields=fields)


if __name__ == "__main__":
    app()
