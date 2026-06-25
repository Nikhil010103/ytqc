"""Ollama automator: install → serve → (sign in) → ensure the model → verify.

Each public step is idempotent and returns StepResult(s) so the wizard can show a
checklist and only act on what's missing. `gemma4:31b-cloud` is a cloud model, so
it needs `ollama signin` (interactive, per-user account) — we detect that need and
surface it as an ACTION rather than failing."""
from __future__ import annotations

import time
from typing import Optional

from ytqc.setup.platform import (Status, StepResult, is_macos, is_windows,
                                 os_name, port_open, run, spawn, which)

HOST = "127.0.0.1"
PORT = 11434
_AUTH_HINTS = ("sign in", "signin", "unauthorized", "not signed in", "401",
               "log in", "authenticate", "ollama.com/signin")


def installed() -> bool:
    return which("ollama") is not None


def serving() -> bool:
    return port_open(HOST, PORT)


def install(console) -> StepResult:
    if installed():
        return StepResult("ollama install", Status.OK, "already installed")
    name = os_name()
    try:
        if name == "macos" and which("brew"):
            console.print("[dim]installing Ollama via Homebrew…[/]")
            run(["brew", "install", "ollama"], timeout=600)
        elif name == "windows" and which("winget"):
            console.print("[dim]installing Ollama via winget…[/]")
            run(["winget", "install", "--id", "Ollama.Ollama", "-e",
                 "--accept-package-agreements", "--accept-source-agreements"], timeout=600)
        elif name == "linux":
            console.print("[dim]installing Ollama via official script…[/]")
            run(["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"], timeout=600)
        else:
            return StepResult(
                "ollama install", Status.ACTION, "not installed",
                hint="install Ollama from https://ollama.com/download, then re-run `ytqc setup`")
    except Exception as exc:
        return StepResult("ollama install", Status.FAIL, f"install failed — {exc}",
                          hint="install manually from https://ollama.com/download")
    if installed():
        return StepResult("ollama install", Status.OK, "installed")
    return StepResult("ollama install", Status.ACTION, "install did not complete",
                      hint="finish installing from https://ollama.com/download, then re-run setup")


def ensure_serving(console) -> StepResult:
    if serving():
        return StepResult("ollama server", Status.OK, f"running on :{PORT}")
    if not installed():
        return StepResult("ollama server", Status.FAIL, "can't start — ollama not installed")
    console.print("[dim]starting `ollama serve`…[/]")
    spawn(["ollama", "serve"])
    for _ in range(20):                      # wait up to ~10s for the port
        if serving():
            return StepResult("ollama server", Status.OK, f"started on :{PORT}")
        time.sleep(0.5)
    return StepResult("ollama server", Status.FAIL, "started but not reachable",
                      hint="run `ollama serve` in a terminal and watch for errors")


def _model_present(model: str) -> bool:
    try:
        r = run(["ollama", "list"], timeout=20)
        return model in (r.stdout or "")
    except Exception:
        return False


def signin(console) -> StepResult:
    """Interactive `ollama signin` (opens a browser; per-user account). Inherits
    the terminal so the user can complete the flow."""
    if not installed():
        return StepResult("ollama sign-in", Status.FAIL, "ollama not installed")
    console.print("[hint]Opening Ollama sign-in — complete it in your browser, then return here.[/]")
    try:
        run(["ollama", "signin"], timeout=300, capture=False)
    except Exception as exc:
        return StepResult("ollama sign-in", Status.ACTION, f"could not launch sign-in — {exc}",
                          hint="run `ollama signin` manually, then re-run `ytqc setup`")
    return StepResult("ollama sign-in", Status.OK, "sign-in completed")


def ensure_model(model: str, console, interactive: bool = True) -> StepResult:
    """Make `model` available. Cloud models may require sign-in first; if a pull
    reports an auth error we sign in (when interactive) and retry once."""
    if not serving():
        return StepResult(f"model {model}", Status.FAIL, "ollama not running")
    if _model_present(model):
        return StepResult(f"model {model}", Status.OK, "available")

    def _pull() -> "tuple[bool, str]":
        try:
            r = run(["ollama", "pull", model], timeout=1800)
            return r.returncode == 0, ((r.stderr or "") + (r.stdout or "")).lower()
        except Exception as exc:
            return False, str(exc).lower()

    console.print(f"[dim]fetching model {model}…[/]")
    ok, out = _pull()
    if not ok and any(h in out for h in _AUTH_HINTS):
        if not interactive:
            return StepResult(f"model {model}", Status.ACTION, "sign-in required",
                              hint="run `ollama signin` (cloud model needs your Ollama account), then re-run setup")
        signin(console)
        ok, out = _pull()
    if ok or _model_present(model):
        return StepResult(f"model {model}", Status.OK, "ready")
    return StepResult(f"model {model}", Status.FAIL, "could not fetch model",
                      hint="check your Ollama account/quota; try `ollama pull " + model + "` manually")


def ensure(model: str, console, interactive: bool = True) -> list[StepResult]:
    """Full chain. Stops surfacing later steps as failures once a blocker hits."""
    results = [install(console)]
    if results[-1].blocking or results[-1].status == Status.ACTION:
        return results
    results.append(ensure_serving(console))
    if results[-1].blocking:
        return results
    results.append(ensure_model(model, console, interactive=interactive))
    return results
