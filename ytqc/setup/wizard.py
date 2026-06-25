"""The `ytqc setup` wizard: drives every dependency from zero to ready, then drops
the user into the chat assistant. Idempotent and re-runnable. Also provides the
`ytqc start` service-boot and the desktop-launcher writer."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Optional

from ytqc.config import CONFIG_PATH, load_config, save_config
from ytqc.setup import checks, chrome, kimi, ollama
from ytqc.setup.platform import (Status, StepResult, chrome_binary, is_windows,
                                 os_name, port_open, spawn)

_SYM = {Status.OK: "[green]✓[/]", Status.ACTION: "[yellow]◐[/]",
        Status.WARN: "[yellow]![/]", Status.FAIL: "[red]✗[/]"}


def _render(console, results: list[StepResult]) -> None:
    for r in results:
        console.print(f"  {_SYM.get(r.status, '?')} {r.name}: {r.message}")
        if r.hint and r.status != Status.OK:
            console.print(f"      [dim]{r.hint}[/]")


def _manages_local_ollama(profile) -> bool:
    return any(h in profile.base_url for h in ("localhost", "127.0.0.1", "0.0.0.0"))


def run_setup(provider: Optional[str] = None, model: Optional[str] = None,
              non_interactive: bool = False, repair: bool = False,
              offer_chat: bool = True) -> bool:
    """Returns True if the environment ends up fully ready. `offer_chat` is set
    False when called from inside the chat (`/setup`) to avoid re-launching it."""
    from rich.console import Console
    console = Console()
    interactive = not non_interactive

    console.print("\n[bold]ytqc setup[/] — getting your machine ready for QC\n")
    from ytqc.setup import guide
    guide.manual_steps_panel(console)      # show the manual touches up front
    console.print("[dim]full guide: `ytqc guide`[/]\n")

    cfg = load_config()
    prov = provider or cfg.active_provider
    try:
        profile = cfg.provider(prov)
    except KeyError as exc:
        console.print(f"[red]error:[/] {exc.args[0] if exc.args else exc}")
        return False
    eff_model = model or profile.model

    all_results: list[StepResult] = []

    # 1. LLM / Ollama
    console.print("[bold]1. AI model (Ollama)[/]")
    if _manages_local_ollama(profile):
        r = ollama.ensure(eff_model, console, interactive=interactive)
    else:
        r = [StepResult("ollama", Status.OK,
                        f"using remote provider {prov!r} — nothing to install")]
    _render(console, r)
    all_results += r

    # 2. kimi-webbridge daemon
    console.print("\n[bold]2. Browser bridge (kimi-webbridge)[/]")
    r = kimi.ensure(console)
    _render(console, r)
    all_results += r

    # 3. Chrome extensions (force-install policy)
    console.print("\n[bold]3. Chrome extensions (kimi + VidIQ)[/]")
    r = chrome.ensure(console)
    _render(console, r)
    all_results += r

    # 4. YouTube sign-in (manual, opened for convenience)
    console.print("\n[bold]4. YouTube sign-in[/]")
    yt = _youtube_step(console)
    _render(console, [yt])
    all_results.append(yt)

    # 5. Persist config
    if not CONFIG_PATH.exists():
        if provider:
            cfg.active_provider = provider
        if model:
            profile.model = model
        save_config(cfg)
        console.print(f"\n[dim]wrote config → {CONFIG_PATH}[/]")

    # 6. Final connectivity check
    console.print("\n[bold]Connectivity check[/]")
    ok = checks.render_doctor(checks.doctor_probes(cfg, provider), console)

    # Summary
    actions = [r for r in all_results if r.status == Status.ACTION]
    fails = [r for r in all_results if r.status == Status.FAIL]
    console.print()
    if ok and not fails and not actions:
        console.print("[green]✓ All set — you're ready to QC.[/]")
        if offer_chat and interactive and console.input(
                "Open the ytqc chat now? [Y/n] ").strip().lower() in ("", "y", "yes"):
            from ytqc.agent import run_chat
            run_chat(provider=provider, model=model)
        elif offer_chat:
            console.print("Run [bold]ytqc[/] anytime to start the assistant.")
        return True

    if fails:
        console.print(f"[red]✗ {len(fails)} blocking issue(s):[/]")
        for r in fails:
            console.print(f"  • {r.name}: {r.message}" + (f" — {r.hint}" if r.hint else ""))
    if actions:
        console.print(f"[yellow]◐ {len(actions)} manual step(s) remaining:[/]")
        for r in actions:
            console.print(f"  • {r.name}: {r.hint or r.message}")
    console.print("\nFinish the steps above, then re-run [bold]ytqc setup[/] (it only fixes what's left).")
    console.print("[dim]need detail on a step? run `ytqc guide`.[/]")
    return False


def _youtube_step(console) -> StepResult:
    """Open YouTube in Chrome so the user can sign in (can't be automated)."""
    chrome_path = chrome_binary()
    if chrome_path:
        spawn([chrome_path, "https://www.youtube.com"])
        return StepResult(
            "youtube sign-in", Status.ACTION, "sign into YouTube in the Chrome window",
            hint="use a dedicated account; YouTube Premium removes ~20s ad waits per video.")
    return StepResult("youtube sign-in", Status.ACTION, "open Chrome and sign into YouTube",
                      hint="Chrome not found — install it, then sign into YouTube.")


# ── `ytqc start`: boot services, then chat ────────────────────────────────────
def boot_services(console) -> None:
    """Best-effort: make sure Ollama + the kimi daemon are up and Chrome is open,
    then the caller launches chat. Quiet — failures surface later via /setup."""
    cfg = load_config()
    try:
        profile = cfg.provider(cfg.active_provider)
        if _manages_local_ollama(profile) and not ollama.serving():
            ollama.ensure_serving(console)
    except Exception:
        pass
    try:
        if kimi.installed() and not kimi.status().get("running"):
            kimi.ensure_running(console)
    except Exception:
        pass
    try:
        cp = chrome_binary()
        if cp:
            spawn([cp])
    except Exception:
        pass


# ── desktop launcher ──────────────────────────────────────────────────────────
def install_launcher(console) -> StepResult:
    desktop = Path.home() / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    try:
        if is_windows():
            path = desktop / "ytqc.bat"
            path.write_text("@echo off\r\nytqc start\r\n")
        else:
            path = desktop / "ytqc.command"
            path.write_text("#!/bin/bash\nexec ytqc start\n")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception as exc:
        return StepResult("launcher", Status.FAIL, f"could not write launcher — {exc}")
    return StepResult("launcher", Status.OK, f"created {path}")
