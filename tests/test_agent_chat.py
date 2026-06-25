"""REPL plumbing: input reading (slash-commands, line continuation) and the
system-prompt guardrails. No LLM, no network."""
import pytest
from rich.console import Console

from ytqc.agent.chat import _build_system_prompt, _read_user_input
from ytqc.config import load_config


class _FakeConsole:
    """Feeds scripted lines to console.input()."""
    def __init__(self, lines):
        self._lines = list(lines)
    def input(self, prompt=""):
        if not self._lines:
            raise EOFError
        return self._lines.pop(0)


def test_read_plain_line():
    assert _read_user_input(_FakeConsole(["qc my channels"])) == "qc my channels"


def test_read_blank_returns_none():
    assert _read_user_input(_FakeConsole([""])) is None


def test_slash_command_passed_through():
    assert _read_user_input(_FakeConsole(["/help"])) == "/help"


def test_line_continuation_with_backslash():
    out = _read_user_input(_FakeConsole(["first line \\", "second line"]))
    assert out == "first line \nsecond line"


def test_eof_propagates():
    with pytest.raises(EOFError):
        _read_user_input(_FakeConsole([]))


def test_system_prompt_has_guardrails():
    sp = _build_system_prompt(load_config())
    low = sp.lower()
    # persona + the engineered guardrails the user asked for
    assert "ytqc" in low
    assert "out of scope" in low or "outside" in low      # out-of-scope handling
    assert "clarify" in low and "guess" in low            # clarify-don't-guess
    assert "never invent" in low or "never estimate" in low  # anti-hallucination
    assert "i can't help with that" in low                # abuse decline
    assert "today:" in low                                # context line


def test_system_prompt_requires_lane_confirmation():
    sp = _build_system_prompt(load_config()).lower()
    assert "lane" in sp
    assert "2 lanes" in sp           # the default the assistant offers
    assert "confirm" in sp           # always-confirm rule
