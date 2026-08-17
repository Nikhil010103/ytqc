"""keep_awake: hold a no-sleep assertion for the length of a run.

A sleeping Mac suspends the browser tabs mid-run; a locked screen alone does
not. These tests pin the contract (opt-out, non-macOS no-op, never fatal) — the
real power assertion is macOS's to make, so `caffeinate` itself is faked.
"""
from __future__ import annotations

import ytqc.utils.keepawake as ka


class _FakeProc:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def _fake_darwin(monkeypatch, spawned: list):
    monkeypatch.setattr(ka.sys, "platform", "darwin")
    monkeypatch.setattr(ka.shutil, "which", lambda name: "/usr/bin/" + name)

    def _popen(cmd, **kw):
        spawned.append(cmd)
        return _FakeProc()

    monkeypatch.setattr(ka.subprocess, "Popen", _popen)


def test_holds_and_releases_the_assertion_on_macos(monkeypatch):
    spawned: list = []
    _fake_darwin(monkeypatch, spawned)
    with ka.keep_awake() as held:
        assert held is True
    assert len(spawned) == 1
    cmd = spawned[0]
    assert cmd[0] == "caffeinate"
    assert "-dimsu" in cmd            # display + idle + disk + system + user-active
    assert "-w" in cmd                # released if we die without cleanup


def test_disabled_spawns_nothing(monkeypatch):
    spawned: list = []
    _fake_darwin(monkeypatch, spawned)
    with ka.keep_awake(enabled=False) as held:
        assert held is False
    assert spawned == []


def test_non_macos_is_a_noop(monkeypatch):
    monkeypatch.setattr(ka.sys, "platform", "linux")
    monkeypatch.setattr(ka.subprocess, "Popen",
                        lambda *a, **k: pytest_fail("must not spawn"))
    with ka.keep_awake() as held:
        assert held is False


def test_missing_caffeinate_is_a_noop(monkeypatch):
    monkeypatch.setattr(ka.sys, "platform", "darwin")
    monkeypatch.setattr(ka.shutil, "which", lambda name: None)
    with ka.keep_awake() as held:
        assert held is False


def test_a_failing_spawn_never_breaks_the_run(monkeypatch):
    monkeypatch.setattr(ka.sys, "platform", "darwin")
    monkeypatch.setattr(ka.shutil, "which", lambda name: "/usr/bin/caffeinate")

    def _boom(*a, **k):
        raise OSError("no fork for you")

    monkeypatch.setattr(ka.subprocess, "Popen", _boom)
    with ka.keep_awake() as held:
        assert held is False


def test_body_exception_still_releases(monkeypatch):
    spawned: list = []
    _fake_darwin(monkeypatch, spawned)
    procs: list = []
    real_popen = ka.subprocess.Popen

    def _popen(cmd, **kw):
        p = real_popen(cmd, **kw)
        procs.append(p)
        return p

    monkeypatch.setattr(ka.subprocess, "Popen", _popen)
    try:
        with ka.keep_awake():
            raise RuntimeError("run blew up")
    except RuntimeError:
        pass
    assert procs and procs[0].terminated is True


def pytest_fail(msg):        # tiny helper so the lambda above stays one line
    raise AssertionError(msg)
