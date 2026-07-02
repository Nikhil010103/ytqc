# 🎬 ytqc — Agentic YouTube QC

Point it at a list of YouTube channels/videos. It opens each one in a real browser, reads the page the way a human QC analyst would — metadata, transcript, frames, comments, stats — and writes a validated QC record (category, brand safety, audience, language, region, engagement) to CSV / Excel.

🧠 2 LLM calls/video · 🌐 real-browser extraction · 🛡️ deterministic safety validator · 📊 CSV + styled Excel

---

## 🚀 Quick start

If you already have **Python 3.10 or newer** and **git**:

```bash
pipx install "git+https://github.com/Nikhil010103/ytqc.git"   # or: pip install "git+https://github.com/Nikhil010103/ytqc.git"
ytqc setup
```

No token, no account, no SSH key. If you're on a fresh machine (nothing installed), or you're not sure about Python/admin rights, follow the full **[end-to-end install](#-full-install-assume-nothing) below** instead — it covers every step.

**Pin a specific version** · **Update:**
```bash
pipx install "git+https://github.com/Nikhil010103/ytqc.git@v0.1.0"   # pin
pipx upgrade ytqc                                                    # update
```

---

## 🧭 Full install (assume nothing)

Pick the path that matches your machine. **The only hard requirement is Python 3.10+** — the tool will not install on 3.9 or older.

> **Do you have admin (can you install apps / type your Mac password)?**
> • **Yes** → [Path A: one-command bootstrap](#path-a--admin-mac--one-command-bootstrap) (easiest).
> • **No** (locked-down work laptop) → [Path B: no-admin install](#path-b--no-admin-mac).
> • **Windows** → [Path C: Windows](#path-c--windows).

### Check what you have first

```bash
python3 --version      # need 3.10+  (3.9 will NOT work)
git --version          # any version is fine
uname -m               # arm64 = Apple Silicon, x86_64 = Intel  (matters for downloads below)
```

---

### Path A — admin Mac → one-command bootstrap

One command installs **everything** (Homebrew → git, Python 3.12, pipx, Chrome → ytqc), then runs setup. You'll be asked for your Mac password (that's the "admin" part).

```bash
curl -fsSL "https://raw.githubusercontent.com/Nikhil010103/ytqc/main/installer/bootstrap.sh" | bash
```

That's it — skip to [the 3 by-hand steps](#-the-3-by-hand-steps-happen-inside-ytqc-setup). If it prints **"Need sudo access … needs to be an Administrator"**, you don't have admin — use **[Path B](#path-b--no-admin-mac)** instead.

---

### Path B — no-admin Mac

No admin, no Homebrew, and your system Python may be too old (3.9). This path installs everything **inside your home folder** — no password needed. Run each block in order.

**B1. Install a modern Python (via Miniforge — no admin).** Homebrew and the python.org installer both need admin; Miniforge does not.

```bash
# Apple Silicon (arm64). For Intel, change arm64 → x86_64 in the URL.
curl -fsSL "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-arm64.sh" -o ~/miniforge.sh
bash ~/miniforge.sh -b -p "$HOME/miniforge3"      # -b = unattended, no prompts
export PATH="$HOME/miniforge3/bin:$PATH"
python --version                                  # should print 3.12.x or newer
```

**B2. Install pipx and put it on PATH.** (Note the spelling: `ensurepath`.)

```bash
python -m pip install --user pipx
python -m pipx ensurepath
# Make miniforge's python AND pipx's bin dir permanent for new terminals:
echo 'export PATH="$HOME/miniforge3/bin:$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
pipx --version                                    # confirms pipx is found
```

**B3. Install ytqc.**

```bash
pipx install "git+https://github.com/Nikhil010103/ytqc.git"
```

**B4. Install Ollama manually (the AI model — no admin).** `ytqc setup` tries to install this via Homebrew, which fails without admin, so do it by hand first. Ollama's app installs fine into your home folder.

```bash
curl -fsSL "https://ollama.com/download/Ollama-darwin.zip" -o ~/Downloads/Ollama.zip
mkdir -p ~/Applications
unzip -o ~/Downloads/Ollama.zip -d ~/Applications/
open ~/Applications/Ollama.app                    # starts the server (menu-bar icon)
# If macOS blocks it as "unidentified developer", clear quarantine then re-open:
#   xattr -dr com.apple.quarantine ~/Applications/Ollama.app && open ~/Applications/Ollama.app

# Put the ollama CLI on PATH for this + future terminals:
echo 'export PATH="$HOME/Applications/Ollama.app/Contents/Resources:$PATH"' >> ~/.zshrc
source ~/.zshrc
ollama --version                                  # confirms the CLI is found
ollama signin                                     # opens browser → sign in (free account)
#   If "unknown command signin": click the Ollama menu-bar icon → Sign in instead.
```

**B5. Chrome.** If Chrome is already installed, `ytqc setup` will detect it. If not and you lack admin, download it from <https://www.google.com/chrome/> and drag **Google Chrome.app** into `~/Applications` (a personal folder, no admin needed).

**B6. Run setup.**

```bash
ytqc setup
```

Then finish [the 3 by-hand steps](#-the-3-by-hand-steps-happen-inside-ytqc-setup) below and press Enter to re-check.

---

### Path C — Windows

Run in **PowerShell**. This uses `winget` (built into Windows 10/11) instead of Homebrew.

```powershell
irm "https://raw.githubusercontent.com/Nikhil010103/ytqc/main/installer/bootstrap.ps1" | iex
```

If you'd rather do it by hand:

```powershell
python -m pip install --user pipx
python -m pipx ensurepath
# close & reopen PowerShell, then:
pipx install "git+https://github.com/Nikhil010103/ytqc.git"
ytqc setup
```

> kimi-webbridge (the browser bridge) has no Windows auto-installer yet — the wizard guides that one step; everything else installs automatically.

---

## ⏸️ The 3 by-hand steps (happen inside `ytqc setup`)

The wizard runs on its own, pauses to prompt you for each, then re-checks and finishes once you're done. They are not separate steps you run yourself.

| Step | When the wizard prompts you | Why |
|------|-----------------------------|-----|
| 1️⃣ | `ollama signin` | the default cloud model is tied to your free Ollama account |
| 2️⃣ | Restart Chrome once | so the auto-installed extensions load |
| 3️⃣ | Sign into YouTube in Chrome | QC opens real pages. Use a dedicated account — YouTube Premium skips ~20s of ad waits per video. |

To fully restart Chrome from the terminal (equivalent to Cmd+Q, then reopen):

```bash
osascript -e 'quit app "Google Chrome"' && open -a "Google Chrome"
```

Then, in the Chrome window, sign into YouTube and click the **kimi-webbridge extension icon** once so it attaches. Back at the `ytqc setup` prompt, press **Enter** to re-check.

✅ Re-run `ytqc setup` anytime — it's idempotent and only fixes what's still missing. Run `ytqc doctor` to check everything is connected, or `ytqc guide` for the full in-tool walkthrough.

---

## ▶️ Then use it

> **Before your first run:** open Chrome (fully quit with `Cmd+Q`, then reopen), sign into YouTube, and click the **kimi-webbridge extension icon** once to activate it. Run `ytqc doctor` to confirm everything is green before starting a batch.

```bash
ytqc                      # chat:  "QC the channels in ~/Desktop/list.csv"
ytqc run -i items.csv     # or go straight to a batch run
```

---

## 🧰 Commands

| command | what it does |
|---------|-------------|
| `ytqc setup` | one-command wizard: install deps + connect Chrome + open chat |
| `ytqc` | open the chat assistant (QC in plain language) |
| `ytqc run -i items.csv` | batch QC run  (`--dry-run`, `--extract-only`, `--limit N`, `--lanes`, `--no-comments`) |
| `ytqc resume <run_id> -i items.csv` | continue an interrupted run (artifacts reused) |
| `ytqc doctor` | connectivity + model health check |
| `ytqc guide` | full in-tool setup guide |
| `ytqc start` | boot services then open chat (desktop-launcher target) |
| `ytqc install-launcher` | create a double-click desktop launcher |
| `ytqc taxonomy` | show the closed category / safety vocabularies |
| `ytqc accuracy --pred results.csv --gold gold.xlsx` | per-field accuracy vs QC-team labels |

📁 Each run writes to `./ytqc_runs/<run_id>/`:

```
results.csv      every QC field, one row per item
results.xlsx     styled — 🟢 safe  🟡 needs review  🔴 unsafe  ⚪ error
state.jsonl      per-item checkpoint (resumable)
artifacts/       raw extraction JSON per item
```

---

<details>
<summary>⚙️ What ytqc setup automates</summary>

On macOS it first installs **Homebrew** (if missing) so the rest can install without prompts.

1. **Ollama** — installs it (Homebrew / winget), starts the server (on a free port if 11434 is busy), fetches `gemma4:31b-cloud`.
2. **kimi-webbridge** — installs and starts the browser-bridge daemon.
3. **Google Chrome** — installs it (Homebrew cask / winget) if it isn't already present.
4. **Chrome extensions** — force-installs **kimi-webbridge**, **VidIQ** + **Adblock for YouTube** via a user-scope Chrome policy (no admin).
5. **Connectivity** — runs the same checks as `ytqc doctor` until everything is green, and (interactively) waits while you finish the 3 manual steps so setup goes green in a single run.

</details>

<details>
<summary>🔬 How it works</summary>

```
input.csv ─► browser producer (serial, paced)          analysis workers (parallel)
             ├ player-response metadata                 ├ deterministic safety pre-gate
             ├ transcript panel scrape (60–120s         ├ Vision Analyst   (1 vision call)
             │  sampled across 5 windows)               ├ Content Analyst  (1 call — taxonomy/
             ├ canvas frames at window midpoints        │  safety/audience prompt)
             ├ likes / comments / channel stats         ├ conditional Judge (conflicts only)
             └ artifacts + JSONL checkpoint     ─────►  ├ deterministic validator (closed
                                                        │  vocab, XOR, risk floor, confidence)
                                                        └ sinks: csv / styled xlsx / es(stub)
```

- **2 LLM calls/video, ~K+1 per channel** (K sampled videos → briefs → weighted vote → synthesizer). Channel brand safety is worst-case across briefs, never averaged.
- The LLM never computes stats and never has the last word on vocabulary — the validator enforces the 35-value tier_1 vocab, the Kids XOR rule, and floors risk levels with deterministic term-gate hits.
- Throughput ~80–100 items/hr mixed (browser-paced for bot hygiene); videos ~16s extraction + ~10s LLM, pipelined.

</details>

<details>
<summary>🔁 Swap the AI provider</summary>

```yaml
# ~/.ytqc/config.yaml
active_provider: ollama-cloud        # or: openai / deepseek / ollama-local
providers:
  openai: {base_url: "https://api.openai.com/v1", api_key: "${OPENAI_API_KEY}",
           model: "gpt-4o-mini", supports_vision: true}
```

Use any OpenAI-compatible API. Non-vision providers skip frame analysis with a confidence penalty. Override per-run with `--provider` / `--model`.

</details>

<details>
<summary>🩺 Troubleshooting</summary>

**Install-time issues**

- **`Need sudo access on macOS … needs to be an Administrator`** (Homebrew) — your account isn't an admin, so the one-command bootstrap can't install Homebrew. Use the no-admin path: **[Path B](#path-b--no-admin-mac)**.
- **`command not found: pipx`** right after installing it — `~/.local/bin` isn't on PATH yet. Run `python -m pipx ensurepath` (note the spelling — not `enurepath`), then **close and reopen the terminal** (or `source ~/.zshrc`). The `ensurepath` step must run *before* you reopen.
- **Python is 3.9 (or older)** — ytqc needs **3.10+** and will fail to install on 3.9. Install a newer Python first: admin → `brew install python`; no admin → Miniforge, see **[B1](#path-b--no-admin-mac)**.
- **`ERROR: Package 'ytqc' requires a different Python`** — same cause: pipx/pip is using an old interpreter. Install ytqc with your 3.10+ python (e.g. `python3.12 -m pip install --user pipx` then `pipx install …`).
- **`llm endpoint: unreachable — [Errno 61] Connection refused`** at the end of setup — Ollama isn't installed/running (Homebrew install was skipped or failed). Install Ollama manually (**[B4](#path-b--no-admin-mac)**), run `ollama signin`, make sure its menu-bar app is running, then re-run `ytqc setup`.
- **`ollama: command not found`** after installing the app — the CLI isn't on PATH. Add it: `export PATH="$HOME/Applications/Ollama.app/Contents/Resources:$PATH"` (adjust if you moved the app), or just launch the app once.

**Runtime issues**

- **Re-run `ytqc setup`** — idempotent; only fixes what's missing.
- **`ytqc doctor`** (or `/check` in chat) — shows whether the browser bridge + AI model are reachable.
- **"browser NOT connected"** — open Chrome, make sure the kimi-webbridge extension is on, and focus the window. If extensions didn't load, fully quit Chrome (`Cmd+Q`) and reopen it once.
- **401 Unauthorized from LLM** — run `ollama signin` in your terminal and complete the browser sign-in; the cloud model requires an active Ollama account session.
- **Ollama "port in use" / `EOF` on `:11434`** — setup auto-moves to a free port; if you hit it manually, quit any stale Ollama (`pkill ollama`, or quit the menu-bar app) and re-run `ytqc setup`.
- **Model errors** — confirm `ollama signin` succeeded and your account has access to `gemma4:31b-cloud`.
- **Captionless videos** (~10–20%) degrade to frames+metadata with a confidence cap and a note in the QC `comment`.
- **Bot-check interstitial** — the run halts and checkpoints (never retries into it); `ytqc resume` later.

</details>

<details>
<summary>🧪 Development</summary>

```bash
git clone https://github.com/Nikhil010103/ytqc.git
cd ytqc
pip install -e ".[dev]"
pytest tests/            # validator XOR matrix + policy floor, sampler math, safety gates,
                         # JSON salvage, channel aggregation, setup/anti-hang robustness
```

</details>
