"""Ollama automator: install → serve → (sign in) → ensure the model → verify.

Each public step is idempotent and returns StepResult(s) so the wizard can show a
checklist and only act on what's missing. `gemma4:31b-cloud` is a cloud model, so
it needs `ollama signin` (interactive, per-user account) — we detect that need and
surface it as an ACTION rather than failing."""
from __future__ import annotations

import os
import socket
import subprocess
import time
from typing import Optional
from urllib.parse import urlparse

from ytqc.setup.platform import (Status, StepResult, is_macos, is_windows,
                                 os_name, port_open, run, spawn, which)

HOST = "127.0.0.1"
DEFAULT_PORT = 11434
# Active port for the Ollama server + CLI. It can move OFF the default when 11434
# is already taken (a stale server, or any unrelated app) so a busy 11434 never
# blocks setup. resolve_port() decides; base_url()/_ollama_env() expose the choice
# to the rest of the tool, and the wizard persists it into config.
PORT = DEFAULT_PORT
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


def base_url() -> str:
    """OpenAI-compatible base URL for the active Ollama port (persisted to config)."""
    return f"http://{HOST}:{PORT}/v1"


def _ollama_env() -> dict:
    """Env so EVERY `ollama` CLI call (serve/list/pull/signin) targets the same port
    we chose — otherwise they'd silently hit the default 11434 and miss our server."""
    return {"OLLAMA_HOST": f"{HOST}:{PORT}"}


def port_from_url(url: str) -> Optional[int]:
    try:
        return urlparse(url or "").port
    except Exception:
        return None


def use_port_from_url(url: str) -> None:
    """Align the active port with a previously-saved config base_url, so re-runs and
    `ytqc start` talk to the port setup actually bound."""
    global PORT
    p = port_from_url(url)
    if p:
        PORT = p


def _free_port() -> int:
    """Ask the OS for an unused TCP port."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((HOST, 0))
        return s.getsockname()[1]
    finally:
        s.close()


def server_healthy(timeout: float = 2.0, port: Optional[int] = None) -> bool:
    """True only if the Ollama HTTP API actually RESPONDS on `port` (default: the
    active PORT) — not just that the port is open. A stale/half-dead server holds
    the port but returns EOF on a request (the false-green 'running' bug). We gate
    on a real /api/version round-trip."""
    import urllib.request
    p = port or PORT
    try:
        with urllib.request.urlopen(f"http://{HOST}:{p}/api/version", timeout=timeout) as r:
            return getattr(r, "status", 200) == 200
    except Exception:
        return False


def resolve_port() -> int:
    """Decide which port the Ollama server uses — tolerant of a busy 11434.

    Priority: explicit OLLAMA_HOST env → the already-aligned port (from config) or
    the default 11434 if either is healthy or free → otherwise a fresh free port
    (the '11434 is taken by another task' case). Sets and returns the module PORT."""
    global PORT
    env = os.environ.get("OLLAMA_HOST", "")
    if env:
        tail = env.rsplit(":", 1)[-1]
        if tail.isdigit():
            PORT = int(tail)
            return PORT
    for cand in (PORT, DEFAULT_PORT):                 # reuse aligned/default if usable
        if cand and (server_healthy(port=cand) or not port_open(HOST, cand)):
            PORT = cand
            return PORT
    PORT = _free_port()                               # default squatted → move off it
    return PORT


def serving() -> bool:
    return server_healthy()


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
    # Pick a usable port FIRST — if 11434 is taken by another task, we move off it
    # so a busy default never blocks setup.
    resolve_port()
    if serving():
        return StepResult("ollama server", Status.OK, f"running on :{PORT}")
    if not installed():
        return StepResult("ollama server", Status.FAIL, "can't start — ollama not installed")
    # If we stayed on the default and it's open-but-unhealthy, it's a stale server.
    stale = (PORT == DEFAULT_PORT) and port_open(HOST, PORT)
    console.print(f"[dim]starting `ollama serve` on :{PORT}…[/]")
    spawn(["ollama", "serve"], env=_ollama_env())
    for _ in range(20):                      # wait for the HTTP API (short per-probe timeout
        if server_healthy(timeout=1.0):      # so a dead-but-open port can't stall the poll)
            return StepResult("ollama server", Status.OK, f"started on :{PORT}")
        time.sleep(0.5)
    if stale:
        return StepResult(
            "ollama server", Status.FAIL, f"port :{PORT} is held but not responding",
            hint="a stale/broken Ollama is squatting the port — quit Ollama from the menu "
                 "bar (or run `pkill ollama`), then re-run `ytqc setup`")
    return StepResult("ollama server", Status.FAIL, f"started on :{PORT} but not reachable",
                      hint=f"run `OLLAMA_HOST={HOST}:{PORT} ollama serve` in a terminal and watch for errors")


def _model_present(model: str) -> bool:
    try:
        r = run(["ollama", "list"], timeout=20, env=_ollama_env())
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
    # Only bring up the desktop app if NOTHING is already serving. Launching it on
    # top of a running `ollama serve` makes the two fight for :11434 and drops the
    # server mid-signin (the 'Head … EOF' failure). If a server is already healthy,
    # `ollama signin` uses it as-is.
    if is_macos() and not serving():
        try:
            run(["open", "-a", "Ollama"], timeout=15)   # best-effort: bring up the app
        except Exception:
            pass
        for _ in range(10):                             # give its server a moment
            if serving():
                break
            time.sleep(0.5)
    console.print("\n[bold]Ollama sign-in needed[/] — the cloud model is tied to your free Ollama "
                  "account.\n[dim]Your browser should open to finish sign-in. If it doesn't, follow the "
                  "URL/prompt shown just below, then come back here.[/]")
    try:
        run(["ollama", "signin"], timeout=300, capture=False, env=_ollama_env())
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
        r = run(["ollama", "pull", model], timeout=timeout, capture=not stream, env=_ollama_env())
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
