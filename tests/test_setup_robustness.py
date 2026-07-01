"""Hermetic tests for the hardened setup wizard — the cloud-model anti-hang flow,
honest doctor probes, Chrome auto-launch, kimi install failure detection, and the
one-invocation recheck/green-gate logic. No subprocess/network/system writes."""
from __future__ import annotations

import pytest

from ytqc.config import DEFAULT_CONFIG
from ytqc.setup import checks, chrome, deps, kimi, ollama, wizard
from ytqc.setup.platform import Status


@pytest.fixture(autouse=True)
def _reset_ollama_port():
    """ollama.PORT is module state mutated by resolve_port()/use_port_from_url();
    reset it around every test so flexible-port cases don't leak into others."""
    ollama.PORT = ollama.DEFAULT_PORT
    yield
    ollama.PORT = ollama.DEFAULT_PORT


class FakeConsole:
    def __init__(self, inputs=None):
        self.lines: list[str] = []
        self._inputs = list(inputs or [])

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    def input(self, prompt: str = "") -> str:
        return self._inputs.pop(0) if self._inputs else "s"

    @property
    def text(self) -> str:
        return "\n".join(self.lines).lower()


def _stub_pull(returns):
    """Return a fake _pull recording its calls and replaying `returns` in order."""
    seq = list(returns)
    calls: list[dict] = []

    def _p(model, timeout, stream=False):
        calls.append({"model": model, "timeout": timeout, "stream": stream})
        return seq.pop(0) if seq else (False, "", False)

    _p.calls = calls
    return _p


# ── _is_cloud detection ──────────────────────────────────────────────────────

@pytest.mark.parametrize("model,expected", [
    ("gemma4:31b-cloud", True),
    ("gpt-oss:120b-cloud", True),
    ("glm-4.6:cloud", True),
    ("gemma4:latest", False),
    ("gpt-4o-mini", False),
    ("", False),
])
def test_is_cloud(model, expected):
    assert ollama._is_cloud(model) is expected


# ── the anti-hang cloud-model flow ───────────────────────────────────────────

def test_cloud_unauth_noninteractive_returns_action_fast(monkeypatch):
    """The live failure, fixed: a signed-out cloud pull must NOT hang — it returns
    an ACTION quickly and never escalates to the full pull."""
    monkeypatch.setattr(ollama, "serving", lambda: True)
    monkeypatch.setattr(ollama, "_model_present", lambda m: False)
    pull = _stub_pull([(False, "", True)])              # probe times out
    monkeypatch.setattr(ollama, "_pull", pull)
    monkeypatch.setattr(ollama, "signin", lambda c: pytest.fail("signin in non-interactive"))

    res = ollama.ensure_model("gemma4:31b-cloud", FakeConsole(), interactive=False)
    assert res.status == Status.ACTION and "sign-in" in res.message
    assert len(pull.calls) == 1                          # probe only — no 1800s pull
    assert pull.calls[0]["timeout"] == ollama.CLOUD_PROBE_TIMEOUT_S


def test_cloud_unauth_interactive_signs_in_then_pulls(monkeypatch):
    monkeypatch.setattr(ollama, "serving", lambda: True)
    monkeypatch.setattr(ollama, "_model_present", lambda m: False)
    pull = _stub_pull([(False, "", True), (True, "", False)])   # probe stalls, full pull ok
    monkeypatch.setattr(ollama, "_pull", pull)
    signed = {"n": 0}

    def _signin(c):
        signed["n"] += 1
        return type("R", (), {"status": Status.OK})()
    monkeypatch.setattr(ollama, "signin", _signin)

    res = ollama.ensure_model("gemma4:31b-cloud", FakeConsole(), interactive=True)
    assert res.status == Status.OK and signed["n"] == 1
    assert len(pull.calls) == 2 and pull.calls[1]["stream"] is True   # full pull streams


def test_cloud_probe_success_skips_signin(monkeypatch):
    monkeypatch.setattr(ollama, "serving", lambda: True)
    monkeypatch.setattr(ollama, "_model_present", lambda m: False)
    monkeypatch.setattr(ollama, "_pull", _stub_pull([(True, "", False)]))
    monkeypatch.setattr(ollama, "signin", lambda c: pytest.fail("signed in despite access"))
    res = ollama.ensure_model("gemma4:31b-cloud", FakeConsole(), interactive=True)
    assert res.status == Status.OK


def test_cloud_fast_nonauth_error_surfaces_as_fail(monkeypatch):
    monkeypatch.setattr(ollama, "serving", lambda: True)
    monkeypatch.setattr(ollama, "_model_present", lambda m: False)
    monkeypatch.setattr(ollama, "_pull",
                        _stub_pull([(False, "error: model 'bogus' not found", False)]))
    monkeypatch.setattr(ollama, "signin", lambda c: pytest.fail("signin on a non-auth error"))
    res = ollama.ensure_model("bogus:7b-cloud", FakeConsole(), interactive=True)
    assert res.status == Status.FAIL and "not found" in res.message


def test_cloud_signin_failure_does_not_retry(monkeypatch):
    monkeypatch.setattr(ollama, "serving", lambda: True)
    monkeypatch.setattr(ollama, "_model_present", lambda m: False)
    pull = _stub_pull([(False, "", True)])
    monkeypatch.setattr(ollama, "_pull", pull)
    monkeypatch.setattr(ollama, "signin",
                        lambda c: type("R", (), {"status": Status.ACTION})())
    res = ollama.ensure_model("gemma4:31b-cloud", FakeConsole(), interactive=True)
    assert res.status == Status.ACTION
    assert len(pull.calls) == 1                          # no pull after failed sign-in


def test_local_model_streams_without_signin(monkeypatch):
    monkeypatch.setattr(ollama, "serving", lambda: True)
    monkeypatch.setattr(ollama, "_model_present", lambda m: False)
    pull = _stub_pull([(True, "", False)])
    monkeypatch.setattr(ollama, "_pull", pull)
    monkeypatch.setattr(ollama, "signin", lambda c: pytest.fail("local model needs no signin"))
    res = ollama.ensure_model("gemma4:latest", FakeConsole(), interactive=True)
    assert res.status == Status.OK
    assert len(pull.calls) == 1 and pull.calls[0]["stream"] is True


# ── kimi install failure detection ───────────────────────────────────────────

def test_kimi_install_network_failure_is_fail(monkeypatch):
    monkeypatch.setattr(kimi, "installed", lambda: False)
    monkeypatch.setattr(kimi, "is_windows", lambda: False)
    monkeypatch.setattr(kimi, "run",
                        lambda *a, **k: type("R", (), {"returncode": 1})())
    res = kimi.install(FakeConsole())
    assert res.status == Status.FAIL and "failed" in res.message.lower()


# ── chrome restart auto-launch ───────────────────────────────────────────────

def test_restart_chrome_autolaunches_when_not_running(monkeypatch):
    monkeypatch.setattr(chrome, "chrome_binary", lambda: "/x/chrome")
    monkeypatch.setattr(chrome, "_chrome_running", lambda: False)
    spawned = []
    monkeypatch.setattr(chrome, "spawn", lambda cmd: (spawned.append(cmd), 123)[1])  # pid
    res = chrome.restart_chrome(FakeConsole())
    assert res.status == Status.OK and spawned == [["/x/chrome"]]


def test_restart_chrome_falls_back_when_launch_fails(monkeypatch):
    """spawn() returning None (launch failed) must NOT report a false green."""
    monkeypatch.setattr(chrome, "chrome_binary", lambda: "/x/chrome")
    monkeypatch.setattr(chrome, "_chrome_running", lambda: False)
    monkeypatch.setattr(chrome, "spawn", lambda cmd: None)
    res = chrome.restart_chrome(FakeConsole())
    assert res.status == Status.ACTION


def test_restart_chrome_asks_when_running(monkeypatch):
    monkeypatch.setattr(chrome, "chrome_binary", lambda: "/x/chrome")
    monkeypatch.setattr(chrome, "_chrome_running", lambda: True)
    monkeypatch.setattr(chrome, "spawn", lambda cmd: pytest.fail("must not respawn a running Chrome"))
    res = chrome.restart_chrome(FakeConsole())
    assert res.status == Status.ACTION


# ── honest doctor probes ─────────────────────────────────────────────────────

class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def test_llm_probe_fails_when_managed_local_model_absent(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp({"data": [{"id": "other"}]}))
    cfg = DEFAULT_CONFIG.model_copy(deep=True)           # ollama-cloud → localhost
    res = checks.llm_probe(cfg, "ollama-cloud")
    assert res.status == Status.FAIL and "not installed" in res.message.lower()


def test_llm_probe_remote_missing_model_is_not_fail(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp({"data": [{"id": "other"}]}))
    cfg = DEFAULT_CONFIG.model_copy(deep=True)
    res = checks.llm_probe(cfg, "openai")               # remote endpoint
    assert res.status != Status.FAIL


def test_kimi_probe_first_run_softens_to_action(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "post",
                        lambda *a, **k: _Resp({"ok": False, "error": "no extension connected"}))
    cfg = DEFAULT_CONFIG.model_copy(deep=True)
    assert checks.kimi_probe(cfg, first_run=True).status == Status.ACTION
    assert checks.kimi_probe(cfg, first_run=False).status == Status.FAIL


# ── wizard green-gate + recheck loop ─────────────────────────────────────────

class _FakeConfigPath:
    """Stands in for CONFIG_PATH so the wizard sees an existing config (not a first
    run) without touching the real ~/.ytqc/config.yaml or patching Path globally."""
    def exists(self):
        return True

    def __str__(self):
        return "/fake/.ytqc/config.yaml"


def _wire_wizard(monkeypatch, probes_fn, console):
    monkeypatch.setattr(wizard, "_console", lambda: console)
    monkeypatch.setattr(wizard, "load_config",
                        lambda *a, **k: DEFAULT_CONFIG.model_copy(deep=True))
    monkeypatch.setattr(wizard, "save_config", lambda *a, **k: None)
    monkeypatch.setattr(wizard, "CONFIG_PATH", _FakeConfigPath())   # not a first run
    monkeypatch.setattr(wizard, "chrome_binary", lambda: "/x/chrome")
    monkeypatch.setattr(wizard, "is_macos", lambda: False)          # skip the brew step
    monkeypatch.setattr(wizard.deps, "ensure_homebrew",
                        lambda c: wizard.StepResult("homebrew", Status.OK, "ok"))
    monkeypatch.setattr(wizard.deps, "ensure_chrome",
                        lambda c: wizard.StepResult("google chrome", Status.OK, "installed"))
    monkeypatch.setattr(wizard.ollama, "ensure",
                        lambda *a, **k: [wizard.StepResult("model", Status.OK, "ready")])
    monkeypatch.setattr(wizard.kimi, "ensure", lambda c: [
        wizard.StepResult("kimi daemon install", Status.OK, "installed"),
        wizard.StepResult("kimi daemon", Status.OK, "running"),
        wizard.StepResult("kimi extension", Status.ACTION, "not connected yet"),
    ])
    monkeypatch.setattr(wizard.kimi, "installed", lambda: False)   # skip nudge in loop
    monkeypatch.setattr(wizard.chrome, "ensure", lambda c: [
        wizard.StepResult("chrome extensions", Status.OK, "policy set"),
        wizard.StepResult("chrome restart", Status.ACTION, "restart Chrome"),
    ])
    monkeypatch.setattr(wizard.checks, "doctor_probes", probes_fn)


def test_wizard_green_when_only_manual_proxies_pending(monkeypatch):
    """ACTIONs that are proven-by-proxy (chrome restart / youtube / kimi extension)
    must NOT keep the summary yellow once the connectivity probe is green."""
    probes = lambda *a, **k: [
        wizard.StepResult("kimi-webbridge", Status.OK, "connected"),
        wizard.StepResult("llm endpoint", Status.OK, "reachable"),
    ]
    _wire_wizard(monkeypatch, probes, FakeConsole())
    ok = wizard.run_setup(non_interactive=True, offer_chat=False)
    assert ok is True


def test_wizard_recheck_loop_reaches_green_in_one_run(monkeypatch):
    calls = {"n": 0}

    def probes(*a, **k):
        calls["n"] += 1
        if calls["n"] >= 3:                              # connects after the user re-checks
            return [wizard.StepResult("kimi-webbridge", Status.OK, "connected"),
                    wizard.StepResult("llm endpoint", Status.OK, "reachable")]
        return [wizard.StepResult("kimi-webbridge", Status.ACTION, "waiting for extension"),
                wizard.StepResult("llm endpoint", Status.OK, "reachable")]

    console = FakeConsole(inputs=[""])                   # press Enter once to re-check
    _wire_wizard(monkeypatch, probes, console)
    ok = wizard.run_setup(non_interactive=False, offer_chat=False)
    assert ok is True and calls["n"] >= 3               # the recheck actually ran


def test_wizard_skips_recheck_when_probes_already_green(monkeypatch):
    """Interactive run, probes already green, but proxy steps are ACTION — the loop
    must NOT prompt (doctor_probes called once, not re-polled)."""
    calls = {"n": 0}

    def probes(*a, **k):
        calls["n"] += 1
        return [wizard.StepResult("kimi-webbridge", Status.OK, "connected"),
                wizard.StepResult("llm endpoint", Status.OK, "reachable")]

    # No inputs queued: if the loop tried to prompt it would default to "skip",
    # but the call count assertion proves it never entered the loop at all.
    _wire_wizard(monkeypatch, probes, FakeConsole())
    ok = wizard.run_setup(non_interactive=False, offer_chat=False)
    assert ok is True and calls["n"] == 1


def test_wizard_not_green_when_real_probe_fails(monkeypatch):
    probes = lambda *a, **k: [
        wizard.StepResult("kimi-webbridge", Status.FAIL, "daemon unreachable"),
        wizard.StepResult("llm endpoint", Status.OK, "reachable"),
    ]
    _wire_wizard(monkeypatch, probes, FakeConsole())
    ok = wizard.run_setup(non_interactive=True, offer_chat=False)
    assert ok is False


def test_wizard_chrome_install_failure_blocks_green(monkeypatch):
    """A failed Google Chrome install must keep setup from reporting 'All set'."""
    probes = lambda *a, **k: [
        wizard.StepResult("kimi-webbridge", Status.OK, "connected"),
        wizard.StepResult("llm endpoint", Status.OK, "reachable"),
    ]
    _wire_wizard(monkeypatch, probes, FakeConsole())
    monkeypatch.setattr(wizard.deps, "ensure_chrome",
                        lambda c: wizard.StepResult("google chrome", Status.FAIL, "install failed"))
    ok = wizard.run_setup(non_interactive=True, offer_chat=False)
    assert ok is False


# ── deps: Homebrew + Chrome bootstrap (clean-machine) ────────────────────────

def test_ensure_homebrew_noop_on_non_macos(monkeypatch):
    monkeypatch.setattr(deps, "is_macos", lambda: False)
    res = deps.ensure_homebrew(FakeConsole())
    assert res.status == Status.OK and "not needed" in res.message


def test_ensure_homebrew_skips_when_present(monkeypatch):
    monkeypatch.setattr(deps, "is_macos", lambda: True)
    monkeypatch.setattr(deps, "brew_path", lambda: "/opt/homebrew/bin/brew")
    monkeypatch.setattr(deps, "run", lambda *a, **k: pytest.fail("must not install when brew present"))
    res = deps.ensure_homebrew(FakeConsole())
    assert res.status == Status.OK and "already" in res.message


def test_ensure_chrome_skips_when_installed(monkeypatch):
    monkeypatch.setattr(deps, "chrome_installed", lambda: True)
    monkeypatch.setattr(deps, "run", lambda *a, **k: pytest.fail("must not install when Chrome present"))
    res = deps.ensure_chrome(FakeConsole())
    assert res.status == Status.OK and "already" in res.message


def test_ensure_chrome_macos_needs_brew_first(monkeypatch):
    monkeypatch.setattr(deps, "chrome_installed", lambda: False)
    monkeypatch.setattr(deps, "os_name", lambda: "macos")
    monkeypatch.setattr(deps, "brew_path", lambda: None)         # brew not available
    res = deps.ensure_chrome(FakeConsole())
    assert res.status == Status.ACTION and "homebrew" in res.message.lower()


def test_ensure_chrome_macos_installs_via_cask(monkeypatch):
    calls = []
    seq = iter([False, True])                                     # missing → installed
    monkeypatch.setattr(deps, "chrome_installed", lambda: next(seq))
    monkeypatch.setattr(deps, "os_name", lambda: "macos")
    monkeypatch.setattr(deps, "brew_path", lambda: "/opt/homebrew/bin/brew")
    monkeypatch.setattr(deps, "run", lambda cmd, **k: calls.append(cmd))
    res = deps.ensure_chrome(FakeConsole())
    assert res.status == Status.OK
    assert any("--cask" in c and "google-chrome" in c for c in calls)


# ── ollama signin brings up the app on macOS (Ankit's "no browser" hang) ─────

def test_signin_opens_app_when_no_server(monkeypatch):
    """No healthy server → bring up the Ollama app, then sign in."""
    monkeypatch.setattr(ollama, "installed", lambda: True)
    monkeypatch.setattr(ollama, "is_macos", lambda: True)
    monkeypatch.setattr(ollama, "serving", lambda: False)
    monkeypatch.setattr(ollama.time, "sleep", lambda *_: None)   # no real wait
    cmds = []
    monkeypatch.setattr(ollama, "run", lambda cmd, **k: cmds.append(cmd))
    res = ollama.signin(FakeConsole())
    assert res.status == Status.OK
    assert ["open", "-a", "Ollama"] in cmds        # app brought up
    assert ["ollama", "signin"] in cmds


def test_signin_does_not_clobber_running_server(monkeypatch):
    """A server is already healthy → must NOT launch the app (the bug that dropped
    the server mid-signin via a port fight). Only `ollama signin` runs."""
    monkeypatch.setattr(ollama, "installed", lambda: True)
    monkeypatch.setattr(ollama, "is_macos", lambda: True)
    monkeypatch.setattr(ollama, "serving", lambda: True)
    cmds = []
    monkeypatch.setattr(ollama, "run", lambda cmd, **k: cmds.append(cmd))
    res = ollama.signin(FakeConsole())
    assert res.status == Status.OK
    assert ["open", "-a", "Ollama"] not in cmds    # did NOT clobber the running server
    assert ["ollama", "signin"] in cmds


# ── ollama server HEALTH check (false-green fix) ─────────────────────────────

def test_ensure_serving_ok_when_healthy(monkeypatch):
    monkeypatch.setattr(ollama, "server_healthy", lambda *a, **k: True)  # default healthy
    monkeypatch.setattr(ollama, "spawn", lambda *a, **k: pytest.fail("must not spawn when healthy"))
    res = ollama.ensure_serving(FakeConsole())
    assert res.status == Status.OK and str(ollama.DEFAULT_PORT) in res.message


def test_ensure_serving_flags_stale_default_when_forced(monkeypatch):
    """If OLLAMA_HOST pins the default and it's open-but-unhealthy → honest FAIL with
    the 'stale server squatting the port' hint (not a false green)."""
    monkeypatch.setenv("OLLAMA_HOST", f"127.0.0.1:{ollama.DEFAULT_PORT}")
    monkeypatch.setattr(ollama, "server_healthy", lambda *a, **k: False)
    monkeypatch.setattr(ollama, "installed", lambda: True)
    monkeypatch.setattr(ollama, "port_open", lambda *a, **k: True)   # port IS held
    monkeypatch.setattr(ollama, "spawn", lambda *a, **k: 123)
    monkeypatch.setattr(ollama.time, "sleep", lambda *_: None)
    res = ollama.ensure_serving(FakeConsole())
    assert res.status == Status.FAIL
    assert "held but not responding" in res.message and "pkill ollama" in res.hint


# ── flexible port: tolerate a busy 11434 (Ankit's machine) ───────────────────

def test_resolve_port_uses_default_when_free(monkeypatch):
    monkeypatch.setattr(ollama, "server_healthy", lambda *a, **k: False)
    monkeypatch.setattr(ollama, "port_open", lambda *a, **k: False)      # nothing anywhere
    assert ollama.resolve_port() == ollama.DEFAULT_PORT


def test_resolve_port_reuses_healthy_default(monkeypatch):
    monkeypatch.setattr(ollama, "server_healthy", lambda *a, **k: True)  # ollama already up
    monkeypatch.setattr(ollama, "spawn", lambda *a, **k: pytest.fail("no spawn needed"))
    assert ollama.resolve_port() == ollama.DEFAULT_PORT


def test_resolve_port_moves_off_busy_default(monkeypatch):
    """11434 taken by another (non-Ollama) task → pick a free port instead."""
    monkeypatch.setattr(ollama, "server_healthy", lambda *a, **k: False)  # not an ollama
    monkeypatch.setattr(ollama, "port_open", lambda *a, **k: True)        # but the port IS busy
    monkeypatch.setattr(ollama, "_free_port", lambda: 11500)
    assert ollama.resolve_port() == 11500 and ollama.PORT == 11500


def test_resolve_port_honors_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:9999")
    assert ollama.resolve_port() == 9999


def test_ensure_serving_binds_free_port_when_default_busy(monkeypatch):
    """End to end: default busy → server comes up on a free port, and every later
    `ollama` call is pointed there via OLLAMA_HOST."""
    state = {"up": False}
    monkeypatch.setattr(ollama, "server_healthy",
                        lambda timeout=2.0, port=None: state["up"] and (port or ollama.PORT) == 11500)
    monkeypatch.setattr(ollama, "port_open", lambda *a, **k: True)        # default busy
    monkeypatch.setattr(ollama, "_free_port", lambda: 11500)
    monkeypatch.setattr(ollama, "installed", lambda: True)
    monkeypatch.setattr(ollama.time, "sleep", lambda *_: None)
    spawned = {}
    def _spawn(cmd, env=None):
        state["up"] = True
        spawned["cmd"], spawned["env"] = cmd, env
        return 123
    monkeypatch.setattr(ollama, "spawn", _spawn)
    res = ollama.ensure_serving(FakeConsole())
    assert res.status == Status.OK and "11500" in res.message
    assert ollama.PORT == 11500
    assert spawned["cmd"] == ["ollama", "serve"]
    assert spawned["env"] == {"OLLAMA_HOST": "127.0.0.1:11500"}


def test_base_url_and_env_track_port():
    ollama.PORT = 11500
    assert ollama.base_url() == "http://127.0.0.1:11500/v1"
    assert ollama._ollama_env() == {"OLLAMA_HOST": "127.0.0.1:11500"}


def test_use_port_from_url_aligns_port():
    ollama.use_port_from_url("http://localhost:12000/v1")
    assert ollama.PORT == 12000
    assert ollama.port_from_url("http://localhost:12000/v1") == 12000
