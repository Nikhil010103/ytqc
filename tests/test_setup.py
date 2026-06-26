"""Hermetic tests for the setup wizard's pure logic — no subprocess/network/system
writes. Live install/policy steps are verified manually (see plan)."""
from __future__ import annotations

from ytqc.setup import checks, chrome
from ytqc.setup import platform as P
from ytqc.setup.platform import (ADBLOCK_YT_EXTENSION_ID, VIDIQ_EXTENSION_ID,
                                 WEBSTORE_UPDATE_URL, Status, StepResult)


def test_os_name_known():
    assert P.os_name() in ("macos", "windows", "linux")


def test_stepresult_flags():
    assert StepResult("x", Status.OK).ok
    assert StepResult("x", Status.FAIL).blocking
    assert not StepResult("x", Status.ACTION).ok
    assert not StepResult("x", Status.ACTION).blocking


def test_chrome_forcelist_entries(monkeypatch):
    # kimi id read dynamically; VidIQ + Adblock-for-YouTube are known store ids.
    # All three get the store URL, in order.
    monkeypatch.setattr(chrome.kimi, "extension_id", lambda: "k" * 32)
    entries = chrome._entries()
    assert entries == [
        f"{'k' * 32};{WEBSTORE_UPDATE_URL}",
        f"{VIDIQ_EXTENSION_ID};{WEBSTORE_UPDATE_URL}",
        f"{ADBLOCK_YT_EXTENSION_ID};{WEBSTORE_UPDATE_URL}",
    ]


def test_chrome_entries_includes_adblock(monkeypatch):
    # the YouTube ad-blocker is force-installed alongside the others
    monkeypatch.setattr(chrome.kimi, "extension_id", lambda: "k" * 32)
    assert any(ADBLOCK_YT_EXTENSION_ID in e for e in chrome._entries())


def test_chrome_entries_dedupe(monkeypatch):
    # if kimi's id ever equals VidIQ's, we don't double-list it (Adblock still present)
    monkeypatch.setattr(chrome.kimi, "extension_id", lambda: VIDIQ_EXTENSION_ID)
    assert chrome._entries() == [
        f"{VIDIQ_EXTENSION_ID};{WEBSTORE_UPDATE_URL}",
        f"{ADBLOCK_YT_EXTENSION_ID};{WEBSTORE_UPDATE_URL}",
    ]


class _Console:
    def __init__(self):
        self.lines: list[str] = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))


def test_render_doctor_overall_bool():
    c = _Console()
    assert checks.render_doctor([StepResult("a", Status.OK, "up")], c) is True
    assert checks.render_doctor([StepResult("a", Status.WARN, "ok", hint="h")], c) is True
    assert checks.render_doctor([StepResult("a", Status.FAIL, "down")], c) is False
    # a single failure among passes flips the overall result
    mixed = [StepResult("a", Status.OK, "up"), StepResult("b", Status.FAIL, "down")]
    assert checks.render_doctor(mixed, c) is False


def test_guide_mentions_all_three_manual_touches():
    from ytqc.setup import guide
    c = _Console()
    guide.render_guide(c)
    text = " ".join(c.lines).lower()
    assert "youtube" in text          # manual touch 1
    assert "ollama signin" in text    # manual touch 2
    assert "restart chrome" in text or "reopen it" in text or "quit chrome" in text  # touch 3
    # all three are also in the structured data
    assert len(guide.MANUAL_STEPS) == 3


def test_manual_steps_panel_runs():
    from ytqc.setup import guide
    c = _Console()
    guide.manual_steps_panel(c)       # must not raise even without rich panel
    assert c.lines
