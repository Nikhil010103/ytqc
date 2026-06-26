# 🎬 ytqc — Agentic YouTube QC

> Point it at a list of YouTube channels/videos. It opens each one in a **real browser**,
> reads the page the way a human QC analyst would — metadata, transcript, frames, comments,
> stats — and writes a validated QC record (category, brand safety, audience, language,
> region, engagement) to **CSV / Excel**.

🧠 2 LLM calls/video · 🌐 real-browser extraction · 🛡️ deterministic safety validator · 📊 CSV + styled Excel

---

## 🚀 Quick start

> 🔑 **One prerequisite either way:** add your **Bitbucket SSH key** (Bitbucket → Personal
> settings → SSH keys) so the install can pull from the private repo.

### 🆕 Brand-new machine (nothing installed)

One command installs **everything** — Homebrew, Python, git, Google Chrome, `ytqc` — then
runs setup. Replace `$BASE` with the internal host the team shares the script from.

```bash
# macOS
curl -fsSL "$BASE/bootstrap.sh" | bash

# Windows (PowerShell)
irm "$BASE/bootstrap.ps1" | iex
```

### 💻 Already have Python 3.10+ & git

```bash
python3 -m pip install --user pipx && python3 -m pipx ensurepath        # one-time, if no pipx
pipx install "git+ssh://git@bitbucket.org/silverpush/yt-qc-agent.git"
ytqc setup                # one command: installs Ollama + Chrome + extensions, connects everything
```

### ▶️ Then use it

```bash
ytqc                      # chat:  "QC the channels in ~/Desktop/list.csv"
ytqc run -i items.csv     # or go straight to a batch run
```

> ⏸️ **The [3 by-hand steps](#-the-3-things-you-do-by-hand--during-ytqc-setup) happen _inside_ `ytqc setup`** — the wizard runs on its own, pauses to prompt you for each, then re-checks and finishes once you're done. They are **not** separate steps you run yourself.

---

## ✋ The 3 things you do by hand — *during* `ytqc setup`

These all happen **inside step 2 above** — they are **not** separate steps. `ytqc setup` runs
on its own until it reaches each one, then **pauses and prompts you**; you complete it and
return. When all three are done, press **Enter** and the wizard re-checks and finishes — no
re-running needed.

| | Step | When the wizard prompts you | Why |
|:--:|---|---|---|
| 1️⃣ | **`ollama signin`** | while fetching the AI model | the default cloud model is tied to your free Ollama account |
| 2️⃣ | **Restart Chrome once** | after it sets the extension policy | so the auto-installed extensions load |
| 3️⃣ | **Sign into YouTube** in Chrome | after Chrome reopens | QC opens real pages. Use a **dedicated** account — YouTube **Premium skips ~20s of ad waits per video**. |

✅ Re-run `ytqc setup` anytime — it's **idempotent** and only fixes what's still missing.
Run `ytqc doctor` to check everything is connected, or `ytqc guide` for the full in-tool walkthrough.

---

## 🧰 Commands

| command | what it does |
|---|---|
| `ytqc setup` | one-command wizard: install deps + connect Chrome + open chat |
| `ytqc` | open the chat assistant (QC in plain language) |
| `ytqc run -i items.csv` | batch QC run &nbsp;(`--dry-run`, `--extract-only`, `--limit N`, `--lanes`, `--no-comments`) |
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
<summary><b>📦 Install options (pip · HTTPS · dev) & updating</b></summary>

Git handles auth with your existing Bitbucket access — no tokens to paste.

```bash
# Recommended — pipx (isolated):
pipx install "git+ssh://git@bitbucket.org/silverpush/yt-qc-agent.git"

# Plain pip (into the current Python / a venv):
pip install "git+ssh://git@bitbucket.org/silverpush/yt-qc-agent.git"

# HTTPS instead of SSH:
pip install "git+https://bitbucket.org/silverpush/yt-qc-agent.git"

# From a local checkout (development):
pip install -e .
```

**Updating:** `pipx upgrade ytqc` &nbsp;(or `pipx reinstall ytqc`); for plain pip, re-install with `--force-reinstall`.

</details>

<details>
<summary><b>⚙️ What <code>ytqc setup</code> automates</b></summary>

On macOS it first installs **Homebrew** (if missing) so the rest can install without prompts.

1. **Ollama** — installs it (Homebrew / winget), starts the server, fetches `gemma4:31b-cloud`.
2. **kimi-webbridge** — installs and starts the browser-bridge daemon.
3. **Google Chrome** — installs it (Homebrew cask / winget) if it isn't already present.
4. **Chrome extensions** — force-installs **kimi-webbridge**, **VidIQ** + **Adblock for YouTube** via a user-scope Chrome policy (no admin).
5. **Connectivity** — runs the same checks as `ytqc doctor` until everything is green, and (interactively) waits while you finish the 3 manual steps so setup goes green in a single run.

</details>

<details>
<summary><b>🔬 How it works</b></summary>

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
<summary><b>🔁 Swap the AI provider</b></summary>

```yaml
# ~/.ytqc/config.yaml
active_provider: ollama-cloud        # or: openai / deepseek / ollama-local
providers:
  openai: {base_url: "https://api.openai.com/v1", api_key: "${OPENAI_API_KEY}",
           model: "gpt-4o-mini", supports_vision: true}
```

Use any OpenAI-compatible API. Non-vision providers skip frame analysis with a confidence penalty.
Override per-run with `--provider` / `--model`.

</details>

<details>
<summary><b>🩺 Troubleshooting</b></summary>

- **Re-run `ytqc setup`** — idempotent; only fixes what's missing.
- **`ytqc doctor`** (or `/check` in chat) — shows whether the browser bridge + AI model are reachable.
- **"browser NOT connected"** — open Chrome, make sure the kimi-webbridge extension is on, and focus the window.
- **Model errors** — confirm `ollama signin` succeeded and your account has access to `gemma4:31b-cloud`.
- **Captionless videos** (~10–20%) degrade to frames+metadata with a confidence cap and a note in the QC `comment`.
- **Bot-check interstitial** — the run halts and checkpoints (never retries into it); `ytqc resume` later.

</details>

<details>
<summary><b>🧪 Development</b></summary>

```bash
pip install -e ".[dev]"
pytest tests/            # 279 tests — validator XOR matrix, sampler math, safety gates,
                         # JSON salvage, channel aggregation, setup/anti-hang robustness
```

</details>
