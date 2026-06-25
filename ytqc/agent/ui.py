"""Claude-Code-style terminal UI for the ytqc chat REPL.

Theme + glyphs + pure render helpers (Rich) and the pinned bottom input box
(prompt_toolkit). Pure helpers are side-effect-free and TTY-free so they unit-test
headless; the prompt_toolkit pieces import lazily and are only used on a real TTY.

Honest fidelity note: this recreates Claude Code's *look* on a Rich +
prompt_toolkit stack (Claude Code is Node/Ink). Most elements are pixel-faithful;
the bottom box is dismissed during processing (the spinner renders in its place)
rather than held visible.
"""
from __future__ import annotations

import os
import sys
from io import StringIO

from rich.box import ROUNDED
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# ── palette ─────────────────────────────────────────────────────────────────
ACCENT = "#c0392b"       # muted brick red — bullets, titles
DIM = "#8a8a8a"          # secondary gray — hints, dimmed args/results
BORDER = "#c0392b"       # muted brick red rounded border
ERROR = "#ff5252"        # bright red for errors

USER = "grey62"          # past user messages — muted, distinct from assistant white

CLAUDE_THEME = Theme({
    "bullet": f"{ACCENT} bold",
    "tool.name": "bold",
    "tool.args": DIM,
    "result": DIM,
    "hint": DIM,
    "welcome.title": f"{ACCENT} bold",
    "welcome.border": BORDER,
    "err": ERROR,
    "user.caret": f"{ACCENT} bold",     # the › on a user line
    "user.msg": USER,                   # the user's message text
})

# ── glyphs / verbs ───────────────────────────────────────────────────────────
_UTF8 = (sys.stdout.encoding or "").lower().startswith("utf")
SPINNER_FRAMES = ["✻", "✢", "✶", "✳", "·"] if _UTF8 else ["*", "+", "x", ".", "·".encode("ascii", "replace").decode()]
BULLET = "⏺" if _UTF8 else "*"
BRANCH = "⎿" if _UTF8 else "\\"
STAR = "✻" if _UTF8 else "*"

WORKING_VERBS = [
    "browser khol raha hoon",
    "youtube video dekh raha hoon",
    "channel ko samajh raha hoon",
    "thumbnail check kar raha hoon",
    "comments padh raha hoon",
    "category soch raha hoon",
    "brand-safety dekh raha hoon",
    "video ka matlab nikaal raha hoon",
    "thoda ruk, AI chal raha hai",
    "aaj bahut kaam hai yaar",
    "data ginn raha hoon",
    "almost ho gaya, bas thoda aur",
    "channel ki vibe samajh raha hoon",
    "AI hoon, thak bhi jaata hoon",
    "results jod raha hoon",
    "sab handle ho raha hai, chill",
]


def make_console() -> Console:
    # highlight=False so tool-arg lines stay uniformly dim (Rich would else
    # auto-color numbers/paths and break the "args are one dim color" look).
    return Console(theme=CLAUDE_THEME, highlight=False)


# ── pure helpers (headless-testable) ─────────────────────────────────────────

def pick_verb(call_index: int) -> str:
    """Deterministic per-call verb (stable for a whole think, not per frame)."""
    return WORKING_VERBS[call_index % len(WORKING_VERBS)]


def spinner_line(frame_idx: int, verb: str, elapsed_s: int) -> str:
    glyph = SPINNER_FRAMES[frame_idx % len(SPINNER_FRAMES)]
    return f"{glyph} {verb}… ({elapsed_s}s · ctrl-c to interrupt)"


def working_status_line(frame_idx: int, phrase: str) -> str:
    """Status line shown above the QC progress bar: animated glyph + a rotating
    phrase. Unlike spinner_line, it omits the elapsed/interrupt suffix — the
    progress bar already shows elapsed time and the run prints its own hint."""
    glyph = SPINNER_FRAMES[frame_idx % len(SPINNER_FRAMES)]
    return f"{glyph} {phrase}…"


def run_status_line(frame_idx: int, phrase: str, extracted: int, analyzed: int,
                    total: int, browser_done: bool) -> str:
    """Phase-aware status line above the progress bar. A QC run has two stages
    that finish at different times: browser extraction (lanes) and LLM analysis
    (workers). When extraction finishes first, the lanes close their Chrome tabs
    and only the (invisible) analysis tail remains — which otherwise looks frozen.
    This line makes the phase explicit: shows extraction progress while lanes run,
    then flips to 'browser done · analyzing N left (no tabs)' once they exit. The
    bar's own completed/total still tracks the analysis count."""
    glyph = SPINNER_FRAMES[frame_idx % len(SPINNER_FRAMES)]
    if browser_done:
        remaining = max(total - analyzed, 0)
        return f"{glyph} {phrase}… · browser done · analyzing {remaining} left (no tabs)"
    return f"{glyph} {phrase}… · extracted {extracted}/{total}"


def _fmt_arg(v) -> str:
    s = v if isinstance(v, str) else repr(v) if not isinstance(v, (int, float, bool)) else str(v)
    s = str(s)
    return s if len(s) <= 40 else s[:37] + "…"


def format_tool_call(name: str, args: dict) -> Text:
    t = Text()
    t.append(f"{BULLET} ", style="bullet")
    t.append(name, style="tool.name")
    t.append("(", style="tool.args")
    parts = [f"{k}: {_fmt_arg(v)}" for k, v in (args or {}).items()]
    t.append(", ".join(parts), style="tool.args")
    t.append(")", style="tool.args")
    return t


_RESULT_SUMMARY = {
    "run_qc": lambda r: f"run {r.get('run_id', '?')}: {r.get('items', 0)} items, "
                        f"{r.get('unsafe', 0)} unsafe, {r.get('needs_review', 0)} review",
    "resume_run": lambda r: f"resumed {r.get('run_id', '?')}: {r.get('items', 0)} items",
    "inspect_input": lambda r: f"{r.get('total', 0)} items "
                               f"({r.get('channels', 0)} ch, {r.get('videos', 0)} vid)"
                               + (f", {len(r['unrecognized'])} ignored"
                                  if r.get('unrecognized') else ""),
    "list_runs": lambda r: f"{r.get('count', 0)} runs",
    "show_results": lambda r: f"{r.get('matched', r.get('total', 0))}/{r.get('total', 0)} rows, "
                              f"{r.get('unsafe', 0)} unsafe",
    "check_setup": lambda r: "connected" if r.get("ok") else "not connected",
    "show_taxonomy": lambda r: f"{len(r.get('tier_1', []))} tier-1 categories",
}


def format_tool_result(name: str, result: dict) -> Text:
    t = Text(f"  {BRANCH}  ")
    if isinstance(result, dict) and result.get("error"):
        t.append(f"error: {result['error']}", style="err")
        return t
    try:
        summary = _RESULT_SUMMARY[name](result)
    except Exception:
        import json
        summary = json.dumps(result, default=str)
        if len(summary) > 80:
            summary = summary[:77] + "…"
    t.append(summary, style="result")
    return t


def welcome_panel(version: str, cwd: str, model: str, tips: str) -> Panel:
    body = Group(
        Text(f"{STAR} Welcome to ytqc", style="welcome.title"),
        Text(""),
        Text(f"  cwd:   {cwd}", style="hint"),
        Text(f"  model: {model}", style="hint"),
        Text(""),
        Text(f"  {tips}", style="hint"),
    )
    return Panel(body, box=ROUNDED, border_style="welcome.border",
                 padding=(0, 1), title=f"[hint]v{version}[/]", title_align="right")


def render_assistant(console: Console, text: str) -> None:
    grid = Table.grid(padding=(0, 1))
    grid.add_column(width=1, no_wrap=True)
    grid.add_column(ratio=1)
    grid.add_row(Text(BULLET, style="bullet"), Markdown(text))
    console.print(grid)


def render_user(console: Console, text: str) -> None:
    """Echo a submitted user message into scrollback — muted color + accent
    caret, with a leading blank line so each turn is visually separated."""
    console.print()
    console.print(Text("› ", style="user.caret") + Text(text, style="user.msg"))


# ── prompt_toolkit bottom input box (TTY only; lazy import) ──────────────────

def make_session():
    """Persistent input history across the session (the box is built per-read
    in read_box_input). Returns a FileHistory or None."""
    try:
        from prompt_toolkit.history import FileHistory
        hist_path = os.path.expanduser("~/.ytqc/history")
        os.makedirs(os.path.dirname(hist_path), exist_ok=True)
        return FileHistory(hist_path)
    except Exception:
        return None


def read_box_input(history=None) -> str:
    """Read one message from a bordered input box that fits inline (no
    screen-bottom gap). Returns the text; raises EOFError (ctrl-D) /
    KeyboardInterrupt (ctrl-C). Built on prompt_toolkit's Frame widget so the
    border is drawn correctly and sized to content."""
    from prompt_toolkit.application import Application, get_app
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import Frame, TextArea

    def _accept(buf):
        get_app().exit(result=buf.text)

    field = TextArea(multiline=False, prompt="› ", accept_handler=_accept, history=history)
    hint = Window(
        FormattedTextControl([("class:hint", "  ? for shortcuts   ·   ctrl-c to interrupt")]),
        height=1,
    )
    root = HSplit([Frame(field), hint])

    kb = KeyBindings()

    @kb.add("c-c")
    def _(event):
        event.app.exit(exception=KeyboardInterrupt)

    @kb.add("c-d")
    def _(event):
        if not field.text:
            event.app.exit(exception=EOFError)

    style = Style.from_dict({
        "frame.border": "fg:#c0392b",   # muted brick red input box border
        "hint": "fg:#8a8a8a",
        "": "",                       # caret/prompt inherits terminal default
    })
    app = Application(
        layout=Layout(root, focused_element=field),
        key_bindings=kb, style=style,
        full_screen=False,            # render inline (no alt-screen, no big gap)
        erase_when_done=True,         # wipe the box after submit; we echo the line above
        mouse_support=False,
    )
    return app.run() or ""


def render_to_str(renderable) -> str:
    """Helper for tests: render a Rich renderable to plain text."""
    buf = StringIO()
    Console(file=buf, theme=CLAUDE_THEME, highlight=False, width=100).print(renderable)
    return buf.getvalue()
