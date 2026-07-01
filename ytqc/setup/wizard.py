"""The `ytqc setup` wizard: drives every dependency from zero to ready, then drops
the user into the chat assistant. Idempotent and re-runnable. Also provides the
`ytqc start` service-boot and the desktop-launcher writer."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.theme import Theme

from ytqc.config import CONFIG_PATH, load_config, save_config
from ytqc.setup import checks, chrome, deps, kimi, ollama
from ytqc.setup.platform import (Status, StepResult, chrome_binary, is_macos,
                                 is_windows, os_name, port_open, spawn)

_SYM = {Status.OK: "[green]✓[/]", Status.ACTION: "[yellow]◐[/]",
        Status.WARN: "[yellow]![/]", Status.FAIL: "[red]✗[/]"}

# Manual touches whose success is PROVEN by the live connectivity probe: once the
# kimi extension answers, Chrome was restarted with the extension and the YouTube
# profile is active. So these must not keep the summary yellow on their own — the
# probe is the source of truth.
_MANUAL_PROXY = {"chrome restart", "youtube sign-in", "kimi extension"}

# A small theme so [hint]/[ok]/[warn]/[err] resolve (the plain Console used before
# left ollama.signin's [hint] markup unstyled).
_WIZARD_THEME = Theme({"hint": "dim", "ok": "green", "warn": "yellow", "err": "red"})


def _console() -> Console:
    return Console(theme=_WIZARD_THEME)


def _pending(results: list[StepResult]) -> bool:
    return any(r.status in (Status.ACTION, Status.FAIL) for r in results)


def _render(console, results: list[StepResult]) -> None:
    for r in results:
        console.print(f"  {_SYM.get(r.status, '?')} {r.name}: {r.message}")
        if r.hint and r.status != Status.OK:
            console.print(f"      [dim]{r.hint}[/]")


def _manages_local_ollama(profile) -> bool:
    return any(h in profile.base_url for h in ("localhost", "127.0.0.1", "0.0.0.0"))


def run_setup(provider: Optional[str] = None, model: Optional[str] = None,
              non_interactive: bool = False, offer_chat: bool = True) -> bool:
    """Returns True if the environment ends up fully ready. `offer_chat` is set
    False when called from inside the chat (`/setup`) to avoid re-launching it.

    Interactive runs finish in ONE invocation: after the automated steps it offers
    a wait+recheck loop so the user can do the manual touches (Chrome restart +
    sign-ins) and have setup verify them now, instead of re-running `ytqc setup`."""
    console = _console()
    interactive = not non_interactive
    first_run = not CONFIG_PATH.exists()

    console.print("\n[bold]ytqc setup[/] — getting your machine ready for QC\n")
    from ytqc.setup import guide
    guide.manual_steps_panel(console)      # show the manual touches up front
    console.print("[dim]full guide: `ytqc guide`[/]\n")

    cfg = load_config()
    prov = provider or cfg.active_provider
    try:
        profile = cfg.provider(prov)
    except KeyError as exc:
        console.print(f"[err]error:[/] {exc.args[0] if exc.args else exc}")
        return False
    eff_model = model or profile.model

    steps: list[StepResult] = []

    # 1. LLM / Ollama (+ Homebrew bootstrap on a clean Mac, so the install isn't manual)
    console.print("[bold]1. AI model (Ollama)[/]")
    if _manages_local_ollama(profile):
        ollama.use_port_from_url(profile.base_url)     # respect a previously-bound port
        r = []
        if is_macos():
            r.append(deps.ensure_homebrew(console))
        r += ollama.ensure(eff_model, console, interactive=interactive)
        # Ollama may have bound a DIFFERENT port (if 11434 was busy) — persist it so
        # the pipeline + connectivity probe hit the same endpoint.
        if ollama.port_from_url(profile.base_url) != ollama.PORT:
            profile.base_url = ollama.base_url()
            save_config(cfg)
            console.print(f"[dim]ollama endpoint → {profile.base_url}[/]")
    else:
        r = [StepResult("ollama", Status.OK,
                        f"using remote provider {prov!r} — nothing to install")]
    _render(console, r)
    steps += r

    # 2. kimi-webbridge daemon
    console.print("\n[bold]2. Browser bridge (kimi-webbridge)[/]")
    r = kimi.ensure(console)
    _render(console, r)
    steps += r

    # 3. Google Chrome (install if missing — QC drives a real Chrome)
    console.print("\n[bold]3. Google Chrome[/]")
    r = [deps.ensure_chrome(console)]
    _render(console, r)
    steps += r

    # 4. Chrome extensions (force-install policy)
    console.print("\n[bold]4. Chrome extensions (kimi + VidIQ + Adblock)[/]")
    r = chrome.ensure(console)
    _render(console, r)
    steps += r

    # 5. YouTube sign-in (manual; guidance only — see _youtube_step)
    console.print("\n[bold]5. YouTube sign-in[/]")
    yt = _youtube_step(console)
    _render(console, [yt])
    steps.append(yt)

    # 6. Persist config — on first run, or whenever explicit --provider/--model is
    #    passed (so a re-run with flags doesn't silently revert to the old config).
    if first_run or provider or model:
        if provider:
            cfg.active_provider = provider
        if model:
            profile.model = model
        save_config(cfg)
        console.print(f"\n[dim]config saved → {CONFIG_PATH}[/]")

    # 7. Connectivity check (+ optional one-invocation recheck loop)
    console.print("\n[bold]Connectivity check[/]")
    probes = checks.doctor_probes(cfg, provider, first_run=first_run)
    _render(console, probes)
    # Offer the recheck loop only when installs succeeded and the live probe is
    # still pending (the proxy step ACTIONs are superseded by a green probe, so
    # they must not trigger an unnecessary prompt).
    if interactive and not [r for r in steps if r.status == Status.FAIL] and _pending(probes):
        probes = _recheck_loop(console, cfg, provider, first_run)

    all_results = steps + probes
    fails = [r for r in all_results if r.status == Status.FAIL]
    # Manual-proxy ACTIONs don't count once the connectivity probe is the judge.
    actions = [r for r in all_results
               if r.status == Status.ACTION and r.name not in _MANUAL_PROXY]

    console.print()
    if not fails and not actions:
        console.print("[ok]✓ All set — you're ready to QC.[/]")
        if offer_chat and interactive and console.input(
                "Open the ytqc chat now? [Y/n] ").strip().lower() in ("", "y", "yes"):
            from ytqc.agent import run_chat
            run_chat(provider=provider, model=model)
        elif offer_chat:
            console.print("Run [bold]ytqc[/] anytime to start the assistant.")
        return True

    if fails:
        console.print(f"[err]✗ {len(fails)} blocking issue(s):[/]")
        for r in fails:
            console.print(f"  • {r.name}: {r.message}" + (f" — {r.hint}" if r.hint else ""))
    if actions:
        console.print(f"[warn]◐ {len(actions)} step(s) remaining:[/]")
        for r in actions:
            console.print(f"  • {r.name}: {r.hint or r.message}")
    console.print("\nFinish the steps above, then re-run [bold]ytqc setup[/] (it only fixes what's left).")
    console.print("[dim]need detail on a step? run `ytqc guide`.[/]")
    return False


def _recheck_loop(console, cfg, provider, first_run) -> list[StepResult]:
    """Let the user complete the manual touches and re-verify in the SAME run.
    Returns the final connectivity probes (whatever state they reach)."""
    console.print("\n[bold]Almost there — finish the manual steps, then I'll verify:[/]")
    console.print("  • [bold]Quit & reopen Chrome[/] (Cmd/Ctrl+Q) so the extensions load")
    console.print("  • [bold]Sign into YouTube[/] in that Chrome window (dedicated account)")
    console.print("  • complete [bold]ollama signin[/] if a browser tab opened")
    probes = checks.doctor_probes(cfg, provider, first_run=first_run)
    while True:
        try:
            ans = console.input(
                "\nPress [bold]Enter[/] to re-check (or [bold]s[/] to skip): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("")
            return probes
        if ans in ("s", "skip", "n", "no"):
            return probes
        try:                                   # daemon may have idled — nudge it
            if kimi.installed() and not kimi.status().get("running"):
                kimi.ensure_running(console)
        except Exception:
            pass
        probes = checks.doctor_probes(cfg, provider, first_run=first_run)
        _render(console, probes)
        if not _pending(probes):
            return probes
        console.print("[dim]still waiting — restart Chrome / sign in, then press Enter to re-check.[/]")


def _youtube_step(console) -> StepResult:
    """Manual: sign into YouTube in Chrome (can't be automated). Guidance only —
    we don't spawn a tab into a Chrome instance that's about to be quit for the
    extension-loading restart; the user opens YouTube after reopening Chrome."""
    if chrome_binary():
        return StepResult(
            "youtube sign-in", Status.ACTION, "sign into YouTube in Chrome",
            hint="after reopening Chrome, sign in with a DEDICATED account; "
                 "YouTube Premium removes ~20s ad waits per video.")
    return StepResult("youtube sign-in", Status.ACTION, "open Chrome and sign into YouTube",
                      hint="Chrome not found — install it, then sign into YouTube.")


# ── `ytqc start`: boot services, then chat ────────────────────────────────────
def boot_services(console) -> None:
    """Best-effort: make sure Ollama + the kimi daemon are up and Chrome is open,
    then the caller launches chat. Quiet — failures surface later via /setup."""
    cfg = load_config()
    try:
        profile = cfg.provider(cfg.active_provider)
        if _manages_local_ollama(profile):
            ollama.use_port_from_url(profile.base_url)   # talk to the port setup bound
            if not ollama.serving():
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
