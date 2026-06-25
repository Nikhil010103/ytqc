"""Connectivity probes shared by `ytqc doctor`, the chat `/setup` check, and the
setup wizard. Each probe returns a StepResult; `render_doctor` reproduces the
original doctor console output and returns the overall ok bool."""
from __future__ import annotations

from typing import Optional

from ytqc.setup.platform import Status, StepResult


def kimi_probe(cfg, first_run: bool = False) -> StepResult:
    """Three states: daemon down, daemon up but no Chrome extension, or ready.
    A trivial evaluate round-trips to the extension, so the error tells us which.

    `first_run` (the setup wizard) softens 'no extension connected' to a pending
    ACTION — on a fresh install that just means 'Chrome hasn't been restarted yet',
    not a failure. `ytqc doctor` / chat `/check` keep it a hard FAIL."""
    import httpx
    try:
        r = httpx.post(cfg.browser.kimi_url,
                       json={"action": "evaluate", "args": {"code": "1+1"},
                             "session": cfg.browser.session}, timeout=5)
        body = r.json()
        err = str(body.get("error", "")).lower()
        if bool(body.get("ok")) or "no tab" in err:
            return StepResult("kimi-webbridge", Status.OK,
                              f"daemon up & browser connected ({cfg.browser.kimi_url})")
        if "no extension connected" in err:
            if first_run:
                return StepResult(
                    "kimi-webbridge", Status.ACTION, "waiting for the Chrome extension",
                    hint="quit & reopen Chrome so the forced extension loads, then sign into "
                         "YouTube and focus the window.")
            return StepResult(
                "kimi-webbridge", Status.FAIL, "browser NOT connected",
                hint="daemon is up but no Chrome extension is attached — open Chrome with "
                     "the kimi-webbridge extension active (its background worker may have gone "
                     "idle; click the extension / focus the window).")
        return StepResult("kimi-webbridge", Status.FAIL, f"NOT OK — {body.get('error', body)}")
    except Exception as exc:
        return StepResult("kimi-webbridge", Status.FAIL, f"daemon unreachable — {exc}",
                          hint=f"is kimi-webbridge running on {cfg.browser.kimi_url}?")


def llm_probe(cfg, provider: Optional[str] = None) -> StepResult:
    """Verify the OpenAI-compatible endpoint is reachable and the model is listed."""
    import httpx
    try:
        profile = cfg.provider(provider)
    except KeyError as exc:
        return StepResult("llm endpoint", Status.FAIL,
                          str(exc.args[0] if exc.args else exc))
    try:
        base = profile.base_url.rstrip("/")
        r = httpx.get(f"{base}/models",
                      headers={"Authorization": f"Bearer {profile.resolved_api_key()}"},
                      timeout=8)
        models = [m.get("id") for m in r.json().get("data", [])]
        listed = profile.model in models
        managed_local = any(h in profile.base_url for h in ("localhost", "127.0.0.1", "0.0.0.0"))
        # A locally-managed Ollama returns its real model list; a configured model
        # that isn't in a NON-EMPTY list is genuinely missing → fail honestly so the
        # wizard never prints "All set" when QC would fail on the first call.
        if models and not listed and managed_local:
            return StepResult(
                "llm endpoint", Status.FAIL,
                f"reachable ({base}) but model {profile.model!r} is NOT installed",
                hint=f"run `ollama pull {profile.model}` (or `ytqc setup`); a cloud model "
                     "needs `ollama signin` first")
        present = listed or not models   # empty list → a remote gateway that hides /models
        msg = f"reachable ({base}) — model {profile.model!r} " + (
            "present" if present else "not listed (endpoint may still serve it)")
        res = StepResult("llm endpoint", Status.OK, msg)
        if not profile.supports_vision:
            res.hint = "provider has no vision — frames will be skipped (confidence penalty)"
            res.status = Status.WARN
        return res
    except Exception as exc:
        return StepResult("llm endpoint", Status.FAIL, f"unreachable — {exc}")


def doctor_probes(cfg, provider: Optional[str] = None, first_run: bool = False) -> list[StepResult]:
    return [kimi_probe(cfg, first_run=first_run), llm_probe(cfg, provider)]


def render_doctor(results: list[StepResult], console) -> bool:
    """Print probe results in the doctor style; return True if nothing failed."""
    ok = True
    for r in results:
        if r.status == Status.OK:
            console.print(f"{r.name}: [green]ready[/] — {r.message}")
        elif r.status == Status.WARN:
            console.print(f"{r.name}: [green]ready[/] — {r.message}")
            if r.hint:
                console.print(f"[yellow]warning:[/] {r.hint}")
        else:
            console.print(f"{r.name}: [red]{r.message}[/]")
            if r.hint:
                console.print(f"  [dim]{r.hint}[/]")
            ok = False
    return ok
