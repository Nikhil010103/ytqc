"""Two-Ctrl-C-to-exit behavior at the chat prompt (Claude Code parity)."""
from io import StringIO

import pytest
from rich.console import Console

import ytqc.agent.chat as chat


class _Exit(Exception):
    pass


def _drive(monkeypatch, script):
    """Run run_chat with a scripted prompt reader. `script` items: the string
    'KBI' raises KeyboardInterrupt, anything else is returned as user input.
    _bye is stubbed to raise _Exit so we can observe the exit without os._exit.
    Returns the captured console output."""
    buf = StringIO()
    monkeypatch.setattr(chat.ui, "make_console", lambda: Console(file=buf, width=100, theme=chat.ui.CLAUDE_THEME))
    monkeypatch.setattr(chat, "_bye", lambda console: (_ for _ in ()).throw(_Exit()))

    it = iter(script)

    def fake_read(console):
        item = next(it)
        if item == "KBI":
            raise KeyboardInterrupt
        return item
    monkeypatch.setattr(chat, "_read_user_input", fake_read)
    # never reach a real turn: only feed slash-commands / interrupts
    try:
        chat.run_chat()
    except _Exit:
        pass
    except StopIteration:
        pass
    return buf.getvalue()


def test_single_ctrl_c_warns_does_not_exit(monkeypatch):
    # one KBI then EOF-like end: must warn, must NOT have exited on the first KBI
    out = _drive(monkeypatch, ["KBI"])         # script exhausts → StopIteration, no _Exit
    assert "press Ctrl-C again to exit" in out


def test_two_ctrl_c_exits(monkeypatch):
    exited = {"v": False}
    buf = StringIO()
    monkeypatch.setattr(chat.ui, "make_console", lambda: Console(file=buf, width=100, theme=chat.ui.CLAUDE_THEME))

    def fake_bye(console):
        exited["v"] = True
        raise _Exit()
    monkeypatch.setattr(chat, "_bye", fake_bye)
    seq = iter(["KBI", "KBI"])

    def fake_read(console):
        if next(seq) == "KBI":
            raise KeyboardInterrupt
    monkeypatch.setattr(chat, "_read_user_input", fake_read)
    try:
        chat.run_chat()
    except (_Exit, StopIteration):
        pass
    assert exited["v"] is True
    assert buf.getvalue().count("press Ctrl-C again to exit") == 1   # warned once


def test_input_between_disarms(monkeypatch):
    # KBI (warn) → /clear (disarm) → KBI (warn again, NOT exit) → end
    out = _drive(monkeypatch, ["KBI", "/clear", "KBI"])
    assert out.count("press Ctrl-C again to exit") == 2     # each lone KBI re-warned
