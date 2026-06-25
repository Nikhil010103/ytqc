"""Canonical, in-tool setup guide. Single source of truth for the setup steps —
especially the THREE manual touches the wizard can't automate. Surfaced via
`ytqc guide`, the chat `/guide` command, and (compactly) at the top of `ytqc setup`."""
from __future__ import annotations

# The three steps the wizard cannot perform for the user. Each: what / why / how.
MANUAL_STEPS = [
    {
        "title": "Sign into YouTube in Chrome",
        "why": "QC opens real YouTube pages; the browser must be logged in. Login can't be automated safely.",
        "how": "When Chrome opens, sign into YouTube. Use a DEDICATED account (QC browsing pollutes watch "
               "history); YouTube Premium on it removes ~20s of ad waiting per video.",
    },
    {
        "title": "Run `ollama signin`",
        "why": "The default model `gemma4:31b-cloud` is a cloud model tied to your own Ollama account.",
        "how": "The wizard launches it automatically — complete the sign-in in your browser, then return. "
               "Or run `ollama signin` yourself and re-run `ytqc setup`.",
    },
    {
        "title": "Restart Chrome once",
        "why": "Chrome only force-installs the kimi-webbridge + VidIQ extensions on launch, after the policy is set.",
        "how": "Fully quit Chrome (Cmd+Q / Ctrl+Q) and reopen it once. The extensions then install automatically.",
    },
]

PREREQUISITES = [
    "Google Chrome installed.",
    "An internet connection (the wizard downloads Ollama + the model).",
    "An Ollama account (free) for the cloud model — created during `ollama signin`.",
    "Ideally a dedicated Google/YouTube account (Premium avoids ad waits).",
]

TROUBLESHOOTING = [
    "Re-run `ytqc setup` anytime — it's idempotent and only fixes what's missing (`--repair` re-applies every step).",
    "`ytqc doctor` (or `/check` in chat) shows whether the browser bridge + AI model are reachable.",
    "\"browser NOT connected\": open Chrome, make sure the kimi-webbridge extension is on, and focus the window.",
    "Model errors: confirm `ollama signin` succeeded and your account has access to `gemma4:31b-cloud`.",
    "Windows: kimi-webbridge may need a manual install — the wizard guides you; everything else is automated.",
]


def manual_steps_panel(console) -> None:
    """Compact, prominent panel listing ONLY the 3 manual touches — shown at the
    start of the wizard so the user knows what to expect."""
    try:
        from rich.panel import Panel
        lines = []
        for i, s in enumerate(MANUAL_STEPS, 1):
            lines.append(f"[bold]{i}. {s['title']}[/]")
            lines.append(f"   {s['how']}")
        body = "\n".join(lines)
        console.print(Panel(body, title="[bold]3 steps you'll do by hand[/]",
                            subtitle="everything else is automatic", border_style="yellow"))
    except Exception:
        console.print("You'll handle 3 steps by hand:")
        for i, s in enumerate(MANUAL_STEPS, 1):
            console.print(f"  {i}. {s['title']} — {s['how']}")


def render_guide(console) -> None:
    """Full setup guide — `ytqc guide` and chat `/guide`."""
    console.print("\n[bold]ytqc — Setup Guide[/]\n")

    console.print("[bold]Quick start[/] (needs Python 3.10+ and git)")
    console.print("  1. Install from the company Bitbucket repo with pipx (isolated) or pip:")
    console.print("       [bold]pipx install \"git+ssh://git@bitbucket.org/WORKSPACE/yt-qc-agent.git\"[/]")
    console.print("       (or [bold]pip install \"git+ssh://git@bitbucket.org/WORKSPACE/yt-qc-agent.git\"[/])")
    console.print("     update later with `pipx upgrade ytqc`")
    console.print("  2. [bold]ytqc setup[/]          one command — installs deps, connects Chrome, opens chat")
    console.print("  3. [bold]ytqc[/]                start the chat assistant anytime\n")

    console.print("[bold]Prerequisites[/]")
    for p in PREREQUISITES:
        console.print(f"  • {p}")
    console.print()

    console.print("[bold]What `ytqc setup` automates[/]")
    console.print("  • Ollama — install, start the server, fetch [bold]gemma4:31b-cloud[/]")
    console.print("  • kimi-webbridge — install + start the browser-bridge daemon")
    console.print("  • Chrome — force-install the [bold]kimi-webbridge[/] + [bold]VidIQ[/] extensions (no admin)")
    console.print("  • Connectivity — verify everything is reachable (same as `ytqc doctor`)\n")

    console.print("[bold yellow]3 steps you do by hand[/] (the wizard opens/guides each):")
    for i, s in enumerate(MANUAL_STEPS, 1):
        console.print(f"  [bold]{i}. {s['title']}[/]")
        console.print(f"     [dim]why:[/] {s['why']}")
        console.print(f"     [dim]how:[/] {s['how']}")
    console.print()

    console.print("[bold]Re-running & launching[/]")
    console.print("  • `ytqc setup` is safe to re-run — it only fixes what's missing (`--repair` forces every step).")
    console.print("  • `ytqc install-launcher` adds a double-click desktop launcher (runs `ytqc start`).")
    console.print("  • `ytqc start` boots the services (Ollama, kimi, Chrome) then opens the chat.\n")

    console.print("[bold]Troubleshooting[/]")
    for t in TROUBLESHOOTING:
        console.print(f"  • {t}")
    console.print()
