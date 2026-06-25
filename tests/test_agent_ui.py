"""UI render helpers — pure, headless (no TTY, no prompt_toolkit)."""
from ytqc.agent import ui


def test_pick_verb_rotates():
    n = len(ui.WORKING_VERBS)
    assert ui.pick_verb(0) == ui.WORKING_VERBS[0]
    assert ui.pick_verb(n) == ui.WORKING_VERBS[0]      # wraps
    assert ui.pick_verb(1) != ui.pick_verb(0)


def test_spinner_line_format():
    line = ui.spinner_line(0, "Cogitating", 7)
    assert ui.SPINNER_FRAMES[0] in line
    assert "Cogitating…" in line
    assert "(7s" in line
    assert "ctrl-c to interrupt" in line


def test_spinner_glyph_cycles_by_frame():
    a = ui.spinner_line(0, "X", 1)[0]
    b = ui.spinner_line(1, "X", 1)[0]
    assert a == ui.SPINNER_FRAMES[0] and b == ui.SPINNER_FRAMES[1]
    # wraps modulo frame count
    assert ui.spinner_line(len(ui.SPINNER_FRAMES), "X", 1)[0] == ui.SPINNER_FRAMES[0]


def test_working_status_line_format():
    line = ui.working_status_line(0, "youtube video dekh raha hoon")
    assert ui.SPINNER_FRAMES[0] in line
    assert "youtube video dekh raha hoon…" in line
    assert "ctrl-c" not in line          # no interrupt suffix on the run status line


def test_run_status_line_phases():
    # extracting phase: shows extraction progress, glyph, no ctrl-c
    ext = ui.run_status_line(0, "channel ko samajh raha hoon",
                             extracted=3, analyzed=1, total=10, browser_done=False)
    assert ui.SPINNER_FRAMES[0] in ext
    assert "extracted 3/10" in ext
    assert "ctrl-c" not in ext

    # browser-done phase: tabs closed, analysis tail remains — must read as alive
    done = ui.run_status_line(0, "results jod raha hoon",
                              extracted=10, analyzed=8, total=10, browser_done=True)
    assert "browser done" in done
    assert "analyzing 2 left" in done
    assert "(no tabs)" in done


def test_format_tool_call():
    t = ui.format_tool_call("run_qc", {"path": "x.csv", "lanes": 2}).plain
    assert t == f"{ui.BULLET} run_qc(path: x.csv, lanes: 2)"


def test_format_tool_call_empty_args():
    assert ui.format_tool_call("list_runs", {}).plain == f"{ui.BULLET} list_runs()"


def test_format_tool_call_truncates_long_values():
    t = ui.format_tool_call("run_qc", {"path": "x" * 80}).plain
    assert "…" in t and len(t) < 80 + 30


def test_format_tool_result_per_tool_summary():
    r = ui.format_tool_result("run_qc", {"run_id": "R1", "items": 2, "unsafe": 1,
                                         "needs_review": 1}).plain
    assert r.strip().startswith(ui.BRANCH)
    assert "run R1: 2 items, 1 unsafe, 1 review" in r


def test_format_tool_result_error():
    r = ui.format_tool_result("inspect_input", {"error": "no file"}).plain
    assert "error: no file" in r


def test_format_tool_result_inspect():
    r = ui.format_tool_result("inspect_input",
                              {"total": 92, "channels": 92, "videos": 0}).plain
    assert "92 items (92 ch, 0 vid)" in r


def test_welcome_panel_contains_context():
    out = ui.render_to_str(ui.welcome_panel("0.1.0", "/tmp/here", "gemma4:31b-cloud", "tip"))
    assert "Welcome to ytqc" in out
    assert "gemma4:31b-cloud" in out
    assert "/tmp/here" in out
    assert "0.1.0" in out


def test_render_assistant_has_bullet():
    from rich.console import Console
    from io import StringIO
    buf = StringIO()
    c = Console(file=buf, theme=ui.CLAUDE_THEME, highlight=False, width=80)
    ui.render_assistant(c, "Hello **world**")
    out = buf.getvalue()
    assert ui.BULLET in out and "Hello" in out


def test_theme_styles_resolve():
    c = ui.make_console()
    for name in ("bullet", "hint", "tool.args", "result", "welcome.title"):
        assert c.get_style(name) is not None


def test_render_user_muted_with_caret():
    from io import StringIO
    from rich.console import Console
    buf = StringIO()
    c = Console(file=buf, theme=ui.CLAUDE_THEME, highlight=False, width=80)
    ui.render_user(c, "hello there")
    out = buf.getvalue()
    assert "›" in out and "hello there" in out


def test_user_styles_resolve():
    c = ui.make_console()
    assert c.get_style("user.msg") is not None      # muted message color
    assert c.get_style("user.caret") is not None     # accent caret
