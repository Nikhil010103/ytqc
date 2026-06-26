"""Ollama automator: install → serve → (sign in) → ensure the model → verify.

Each public step is idempotent and returns StepResult(s) so the wizard can show a
checklist and only act on what's missing. `gemma4:31b-cloud` is a cloud model, so
it needs `ollama signin` (interactive, per-user account) — we detect that need and
surface it as an ACTION rather than failing."""
from __future__ import annotations

import subprocess
import time
from typing import Optional

from ytqc.setup.platform import (Status, StepResult, is_macos, is_windows,
                                 os_name, port_open, run, spawn, which)

HOST = "127.0.0.1"
PORT = 11434
_AUTH_HINTS = ("sign in", "signin", "unauthorized", "not signed in", "401",
               "log in", "authenticate", "ollama.com/signin")

# A signed-in cloud model registers in seconds; an UNAUTHENTICATED cloud pull
# either errors fast or HANGS. We cap the first attempt at this bound so the
# wizard can never freeze for the full pull timeout (the historical failure).
CLOUD_PROBE_TIMEOUT_S = 30
PULL_TIMEOUT_S = 1800        # full pull — local models can be multi-GB


def _is_cloud(model: str) -> bool:
    """Ollama cloud models are tagged `…-cloud` (e.g. gemma4:31b-cloud) or `:cloud`."""
    m = (model or "").strip().lower()
    return m.endswith("-cloud") or m.endswith(":cloud")


def installed() -> bool:
    return which("ollama") is not None


def serving() -> bool:
    return port_open(HOST, PORT)


def install(console) -> StepResult:
    if installed():
        return StepResult("ollama install", Status.OK, "already installed")
    name = os_name()
    try:
        # capture=False → the installer's own progress shows live, so a multi-minute
        # download never looks frozen (a real first-run complaint).
        if name == "macos" and which("brew"):
            console.print("[dim]installing Ollama via Homebrew (progress below)…[/]")
            run(["brew", "install", "ollama"], timeout=600, capture=False)
        elif name == "windows" and which("winget"):
            console.print("[dim]installing Ollama via winget (progress below)…[/]")
            run(["winget", "install", "--id", "Ollama.Ollama", "-e",
                 "--accept-package-agreements", "--accept-source-agreements"],
                timeout=600, capture=False)
        elif name == "linux":
            console.print("[dim]installing Ollama via official script (progress below)…[/]")
            run(["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
                timeout=600, capture=False)
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
    """Interactive `ollama signin` (per-user account). Inherits the terminal so the
    user can complete the flow. The Ollama desktop app brokers the browser hand-off,
    so on macOS we make sure it's running first — otherwise the sign-in can sit there
    with no window."""
    if not installed():
        return StepResult("ollama sign-in", Status.FAIL, "ollama not installed")
    if is_macos():
        try:
            run(["open", "-a", "Ollama"], timeout=15)   # best-effort: bring up the app
        except Exception:
            pass
    console.print("\n[bold]Ollama sign-in needed[/] — the cloud model is tied to your free Ollama "
                  "account.\n[dim]Your browser should open to finish sign-in. If it doesn't, follow the "
                  "URL/prompt shown just below, then come back here.[/]")
    try:
        run(["ollama", "signin"], timeout=300, capture=False)
    except Exception as exc:
        return StepResult("ollama sign-in", Status.ACTION, f"could not launch sign-in — {exc}",
                          hint="run `ollama signin` manually, then re-run `ytqc setup`")
    return StepResult("ollama sign-in", Status.OK, "sign-in completed")


def _pull(model: str, timeout: float, stream: bool = False) -> "tuple[bool, str, bool]":
    """Run `ollama pull`. Returns (ok, output_lower, timed_out).

    stream=True shows ollama's native download progress live (output NOT captured,
    so callers must not scan it). stream=False captures output for auth-hint
    detection. A timeout returns timed_out=True (the child is killed by run())."""
    try:
        r = run(["ollama", "pull", model], timeout=timeout, capture=not stream)
        out = "" if stream else ((r.stderr or "") + (r.stdout or "")).lower()
        return r.returncode == 0, out, False
    except subprocess.TimeoutExpired:
        return False, "", True
    except Exception as exc:
        return False, str(exc).lower(), False


def ensure_model(model: str, console, interactive: bool = True) -> StepResult:
    """Make `model` available — without ever hanging.

    Cloud models need `ollama signin` first. Rather than firing a 30-minute pull
    and hoping it errors with a recognizable auth string (the old behavior, which
    froze the wizard), we probe with a SHORT bounded pull: a signed-in cloud model
    registers in seconds, while an unauthenticated one errors fast or stalls — both
    capped here and treated as 'sign in, then pull for real'."""
    if not serving():
        return StepResult(f"model {model}", Status.FAIL, "ollama not running")
    if _model_present(model):
        return StepResult(f"model {model}", Status.OK, "available")

    def _full_pull() -> bool:
        console.print(f"[dim]downloading model {model} (first time can take a while)…[/]")
        ok, _out, _t = _pull(model, timeout=PULL_TIMEOUT_S, stream=True)
        return ok or _model_present(model)

    def _need_signin_action() -> StepResult:
        return StepResult(f"model {model}", Status.ACTION, "sign-in required",
                          hint="run `ollama signin` (the cloud model needs your free Ollama "
                               "account), then re-run `ytqc setup`")

    if _is_cloud(model):
        console.print(f"[dim]checking access to cloud model {model}…[/]")
        ok, out, timed_out = _pull(model, timeout=CLOUD_PROBE_TIMEOUT_S)
        if ok or _model_present(model):
            return StepResult(f"model {model}", Status.OK, "ready")
        if timed_out or any(h in out for h in _AUTH_HINTS):
            if not interactive:
                return _need_signin_action()
            res = signin(console)
            if res.status != Status.OK:
                return res                         # don't retry into another hang
            return (StepResult(f"model {model}", Status.OK, "ready") if _full_pull()
                    else StepResult(f"model {model}", Status.FAIL,
                                    "could not fetch model after sign-in",
                                    hint=f"try `ollama pull {model}` manually to see the error"))
        # Fast failure that isn't an auth issue (e.g. unknown model name) → surface it.
        return StepResult(f"model {model}", Status.FAIL,
                          f"could not fetch cloud model — {out.strip()[:160] or 'unknown error'}",
                          hint=f"confirm the model name is correct, then try `ollama pull {model}` manually")

    # Local model: no sign-in; stream the (possibly large) download.
    return (StepResult(f"model {model}", Status.OK, "ready") if _full_pull()
            else StepResult(f"model {model}", Status.FAIL, "could not fetch model",
                            hint=f"try `ollama pull {model}` manually to see the error"))


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
