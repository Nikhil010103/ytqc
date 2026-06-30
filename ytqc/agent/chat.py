"""The interactive chat REPL — `ytqc` with no args lands here.

Rich-based prompt + spinner (the cloud model is ~30s/turn, so the spinner and
expectation-setting matter), slash-commands, multi-line input, Ctrl-C/Ctrl-D
handling, and conversation history across turns. The system prompt is the
engineered persona + guardrails the agent runs under."""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

from rich.console import Console
from rich.live import Live
from rich.text import Text

from ytqc import __version__
from ytqc.agent import ui
from ytqc.agent.loop import AgentLLM, run_turn
from ytqc.agent.tools import AgentContext, ToolRegistry
from ytqc.config import CONFIG_PATH, load_config


def _build_system_prompt(cfg) -> str:
    qc_model = cfg.provider(cfg.active_provider).model
    today = time.strftime("%Y-%m-%d")
    return f"""You are **ytqc**, a friendly assistant for an adtech TechOps/QC team. You help people quality-check YouTube channels and videos for ad brand-safety — categorizing content, flagging unsafe material, and pulling stats — all through a real browser-based pipeline.

## Voice
Talk like a helpful, capable colleague: warm, plain-spoken, concise. Greet the user naturally, acknowledge what they ask before you act, and keep a light conversational thread across the session (refer back to earlier runs and requests when it helps). Never sound robotic and never read out raw JSON — translate tool results into a sentence or two. Mirror the user's own wording. A little personality is welcome; empty filler ("Let me check…" with nothing after) is not.

## What you can do
- Start a QC run over a file of channel/video ids, or an explicit list of ids.
- Peek at an input file first to report what's in it.
- Show or filter the results of a run (e.g. just the unsafe ones, or items needing review).
- List previous runs, or resume one that was interrupted.
- Check that the browser bridge and the AI model are connected.
- Show the content-category and brand-safety vocabularies.

## Out of scope (decline warmly, never lecture)
Your job is YouTube QC and nothing else. If asked for general coding help, browsing arbitrary sites, reading files unrelated to QC input, math, trivia, opening apps, or anything off-topic — don't pretend and don't attempt it. Briefly say it's outside what you do and steer back to what you CAN help with, ideally tied to their goal. Keep it short and friendly, e.g.: "That's outside my lane — I'm here to QC YouTube channels and videos. If you've got a list to vet, point me at it." Decline misuse of the pipeline (harassment, targeting individuals, evading platform protections beyond legitimate QC) with a quick, friendly "I can't help with that."

## How to behave
- **Pasted lists: pass them verbatim and preview before running.** When the user pastes channel/video ids (not a file path) — even messy, multi-column lines like `id,channel,US - Name`, URLs, or @handles — pass the raw text **verbatim** as the `ids` argument; never pre-split, re-type, or hand-pick ids yourself (the tools extract and dedupe canonical YouTube ids for you). First call `inspect_input(ids=…)` to confirm how many UNIQUE channels/videos were found, then tell the user that count and mention anything in `unrecognized` or `deduped`. If `unrecognized` is non-empty (e.g. a bare name with no id), **ask the user for that channel's id or URL — never guess it.** Then settle lanes and run.
- **Always confirm the lane count before a QC run.** Browser "lanes" are the parallel tabs used to extract — more is NOT always faster (2 is the sweet spot on most machines). Before you start any run, confirm how many lanes to use: if the user already gave a number, acknowledge it; if they didn't, ask them and offer the default of **2 lanes**. Do not call run_qc until the lane count is settled, and pass it explicitly as the `lanes` argument.
- **Always ask where to save the output before a QC run.** The run will not start without an output folder. If the user hasn't told you where to save, ask for a folder path (you can suggest `{cfg.output_dir}` as the default). Do not call run_qc until you have it, and pass it as the `output_dir` argument. If they gave one earlier this session, reuse it without re-asking unless they want to change it. A save location is a **folder**, so it has no file extension — if the user gives something that ends in `.csv`, `.xlsx`, `.excel`, etc., treat it as a likely slip: don't invent a path, just confirm the folder they meant (e.g. the parent directory). Results are always written as `<folder>/<run_id>/results.csv`.
- **After a run finishes, tell the user where the results were saved.** Every successful run_qc result includes the output path — always state it in your summary (e.g. "saved to …/results.csv") so the user knows where to find the file.
- **Clarify, don't guess.** If a request is missing something you need — no file path, an unclear "which run", a vague "do the thing" — ask ONE short question instead of guessing or calling a tool with made-up arguments. If a path doesn't resolve, say so and ask for the right one. Only call a tool when you actually have what it needs.
- **Never invent facts.** Every run id, count, category, safety verdict, and stat comes from a tool result — never estimate or make them up. If you don't have it, call the tool. If a tool returns an error, explain it plainly and suggest the next step.
- **Never claim a run is in progress without proof.** A QC run is "running" only if you called `run_qc` THIS turn and are awaiting its result. A tool result of `status: cancelled` (or a prior interrupted turn) means **nothing is running** — do not say it's "already started" or "working on it". If the user repeats a request after a cancellation, treat it as a fresh ask and call `run_qc` again. When in doubt, call `list_runs`/`show_results` to check rather than assuming.
- **Runs take time.** A QC run opens browser tabs and runs for minutes (longer for big files); a live progress bar shows during it. When you start one, say it's underway and that you'll summarize when it's done. Afterward, give a short summary — the standout numbers and what to look at — not a dump.

## Context
- Output directory for runs: {cfg.output_dir}
- QC analysis model: {qc_model}
- Today: {today} (use this for "the last run", "today", etc.)
The full category vocabularies are large — call the taxonomy tool if the user asks about categories rather than listing them from memory."""


_TIPS = "Ask me to QC channels/videos in plain language. /help for commands."

_HELP = """[bold]commands[/]
  /help    show this
  /runs    list recent QC runs
  /setup   run the setup wizard (install/connect dependencies)
  /guide   show the setup guide (incl. the manual steps)
  /check   quick browser + AI connectivity check
  /clear   start a fresh conversation
  /exit    quit (or Ctrl-D)

[bold]try saying[/]
  • qc the channels in ~/Desktop/channels.csv with 2 lanes
  • what's in ./trending_videos.csv?
  • show me the unsafe ones from the last run
  • is the browser connected?"""


def _read_user_input(console: Console) -> str | None:
    """Read one prompt. Returns the text, or None if a slash-command was handled
    inline (caller should loop without hitting the LLM). Supports trailing-\\
    line continuation. Raises EOFError/KeyboardInterrupt to exit."""
    first = console.input("[bold green]›[/] ").strip()
    if not first:
        return None
    if first.startswith("/"):
        return first                      # slash-commands handled by caller
    lines = [first]
    while lines[-1].endswith("\\"):
        lines[-1] = lines[-1][:-1]
        lines.append(console.input("[dim]…[/] "))
    return "\n".join(lines).strip()


class TurnRenderer:
    """Coordinates the Claude-style spinner (Rich Live + refresher thread) and
    renders tool-call blocks from loop events. Sequenced so the spinner is never
    live at the same time as run_qc's own Progress bar (it stops on think_stop /
    on_status(None))."""

    def __init__(self, console: Console):
        self.console = console
        self._live: Live | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._verb = ui.WORKING_VERBS[0]
        self._t0 = 0.0
        self._frame = 0
        self._call_index = 0

    def reset_calls(self) -> None:
        self._call_index = 0

    # ── on_status channel (legacy: spinner show/hide) ──
    def on_status(self, label) -> None:
        if label is None:                       # long-running tool incoming → free the console
            self._stop_spinner()

    # ── on_event channel (structured rendering) ──
    def on_event(self, kind: str, **data) -> None:
        if kind == "think_start":
            self._verb = ui.pick_verb(self._call_index)
            self._call_index += 1
            self._start_spinner()
        elif kind == "think_stop":
            self._stop_spinner()
        elif kind == "tool_start":
            self.console.print(ui.format_tool_call(data["name"], data.get("args") or {}))
        elif kind == "tool_end":
            self.console.print(ui.format_tool_result(data["name"], data.get("result") or {}))

    def abort(self) -> None:
        self._stop_spinner()

    # ── spinner internals ──
    def _start_spinner(self) -> None:
        if self._live is not None:
            return
        self._stop.clear()
        self._t0 = time.monotonic()
        self._frame = 0
        self._live = Live(self._render(), console=self.console, refresh_per_second=12,
                          transient=True)
        self._live.start()
        self._thread = threading.Thread(target=self._tick, daemon=True)
        self._thread.start()

    def _stop_spinner(self) -> None:
        if self._live is None:
            return
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)
        self._live.stop()
        self._live = None
        self._thread = None

    def _render(self) -> Text:
        elapsed = int(time.monotonic() - self._t0)
        return Text(ui.spinner_line(self._frame, self._verb, elapsed), style="bullet")

    def _tick(self) -> None:
        while not self._stop.wait(0.12):
            self._frame += 1
            if self._live is not None:
                self._live.update(self._render())


def _bye(console: Console) -> None:
    """Quit the chat cleanly. An interrupted QC run can leave non-daemon
    ThreadPoolExecutor (channel-brief) worker threads blocked on Ollama; Python's
    atexit would then hang join()ing them and a stray Ctrl-C prints an ugly
    'Exception ignored on threading shutdown' trace. Everything is already shut
    down (tabs closed, results checkpointed), so hard-exit past that join."""
    console.print("[hint]bye[/]")
    try:
        console.file.flush()
    except Exception:
        pass
    os._exit(0)


def _setup_chat_logging() -> None:
    """Route library/pipeline logging to a file so background-thread log records
    never write raw to the terminal. The chat REPL owns the screen via Rich's
    Live progress and prompt_toolkit's input box; a stray stderr write from a
    worker thread (e.g. a kimi-webbridge retry warning) would corrupt that
    render. Without any root handler, Python's logging.lastResort handler writes
    to stderr — so we install a FileHandler and clear other handlers to suppress
    it. Logs land in ~/.ytqc/ytqc.log."""
    try:
        log_dir = os.path.expanduser("~/.ytqc")
        os.makedirs(log_dir, exist_ok=True)
        handler = logging.FileHandler(os.path.join(log_dir, "ytqc.log"))
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(name)s %(levelname)s [%(threadName)s] %(message)s"))
        root = logging.getLogger()
        for h in list(root.handlers):       # drop any stream handlers → no stderr writes
            root.removeHandler(h)
        root.addHandler(handler)
        root.setLevel(logging.WARNING)
    except Exception:
        # logging must never break the REPL; worst case we fall back to default
        pass


def run_chat(provider: str | None = None, model: str | None = None) -> None:
    _setup_chat_logging()
    cfg = load_config()
    console = ui.make_console()
    a_provider = provider or getattr(cfg, "agent_provider", None) or cfg.active_provider
    try:
        a_profile = cfg.provider(a_provider)
    except KeyError as exc:
        console.print(f"[err]error:[/] {exc.args[0] if exc.args else exc}")
        return
    a_model = model or getattr(cfg, "agent_model", None) or a_profile.model

    llm = AgentLLM(a_profile, a_model)
    ctx = AgentContext(
        cfg=cfg, console=console, output_dir=cfg.output_dir,
        confirm=lambda msg: console.input(f"[hint]{msg}[/] ").strip().lower() in ("y", "yes"),
    )
    registry = ToolRegistry(ctx)
    system = _build_system_prompt(cfg)
    messages: list = [{"role": "system", "content": system}]
    renderer = TurnRenderer(console)

    console.print(ui.welcome_panel(__version__, os.getcwd(), a_model, _TIPS))

    # First run: no config yet → point the user at the one-command setup wizard.
    if not CONFIG_PATH.exists():
        console.print("[hint]Looks like a first run — type [bold]/setup[/] (or run "
                      "[bold]ytqc setup[/]) to install dependencies and connect Chrome. "
                      "See [bold]/guide[/] for the full walkthrough.[/]")

    use_box = sys.stdin.isatty() and sys.stdout.isatty()
    history = ui.make_session() if use_box else None
    interrupt_at = 0.0                    # timestamp of a pending first Ctrl-C

    while True:
        try:
            if use_box:
                user = ui.read_box_input(history)
                if user and user.strip():
                    # the box is erased on submit — echo the line into scrollback,
                    # muted + separated so it reads as a distinct past turn
                    ui.render_user(console, user.strip())
            else:
                user = _read_user_input(console)
        except EOFError:                  # Ctrl-D → exit immediately
            console.print("")
            _bye(console)
        except KeyboardInterrupt:
            # Ctrl-C at the prompt: first press warns, a second within 3s exits
            # (Claude Code parity — a lone Ctrl-C doesn't quit).
            if time.monotonic() - interrupt_at < 3.0:
                console.print("")
                _bye(console)
            interrupt_at = time.monotonic()
            console.print("[hint](press Ctrl-C again to exit)[/]")
            continue
        except Exception as exc:
            # The box failed on this terminal → degrade to the plain prompt.
            # Only the box path is recoverable this way: if we're already on the
            # plain prompt, an unexpected error must propagate rather than become
            # a silent busy-loop (re-calling the failing reader forever).
            if not use_box:
                raise
            console.print(f"[hint](input box unavailable: {exc} — using a simple prompt)[/]")
            use_box = False
            continue
        interrupt_at = 0.0                # any real input disarms the exit
        if user is None or not user.strip():
            continue
        user = user.strip()
        if user == "?":                      # the hint promises "? for shortcuts"
            console.print(_HELP)
            continue
        if user.startswith("/"):
            cmd = user[1:].split()[0].lower() if user[1:].split() else ""
            if cmd in ("exit", "quit", "q"):
                _bye(console)
            if cmd == "help":
                console.print(_HELP)
            elif cmd == "clear":
                messages = [{"role": "system", "content": system}]
                console.print("[hint]conversation cleared[/]")
            elif cmd == "runs":
                _print_runs(console, registry)
            elif cmd == "setup":
                from ytqc.setup.wizard import run_setup
                run_setup(provider=provider, model=model, offer_chat=False)
            elif cmd == "guide":
                from ytqc.setup.guide import render_guide
                render_guide(console)
            elif cmd == "check":
                registry.dispatch("check_setup", {})
            else:
                console.print(f"[hint]unknown command /{cmd} — try /help[/]")
            continue

        messages.append({"role": "user", "content": user})
        renderer.reset_calls()
        try:
            text, _ = run_turn(llm, registry, messages,
                               on_status=renderer.on_status, on_event=renderer.on_event)
        except KeyboardInterrupt:
            renderer.abort()
            console.print("[hint]cancelled[/]")
            # Keep `messages` in a valid state so the next turn doesn't choke or
            # hallucinate. run_turn already appended cancelled tool-results for an
            # interrupted tool call, so a trailing "tool" message is complete and
            # kept. Only clean up the two invalid trailing shapes:
            if messages and messages[-1]["role"] == "user":
                messages.pop()                              # interrupted while thinking → disarm
            elif (messages and messages[-1]["role"] == "assistant"
                  and messages[-1].get("tool_calls")):
                messages.pop()                              # dangling tool-call with no results
            continue
        except Exception as exc:
            renderer.abort()
            console.print(f"[err]agent error:[/] {exc}")
            continue
        finally:
            renderer.abort()
        if text:
            ui.render_assistant(console, text)


def _print_runs(console: Console, registry) -> None:
    from rich.table import Table
    out = registry.dispatch("list_runs", {})
    runs = out.get("runs", [])
    if not runs:
        console.print("[hint]no runs yet[/]")
        return
    t = Table(title=f"recent runs ({out.get('count', len(runs))} total)")
    t.add_column("run id"); t.add_column("items", justify="right")
    t.add_column("unsafe", justify="right"); t.add_column("when")
    for r in runs:
        t.add_row(r["run_id"], str(r["items"]), str(r["unsafe"]), r["when"])
    console.print(t)
