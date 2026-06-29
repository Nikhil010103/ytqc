# 🎬 ytqc — Agentic YouTube QC

Point it at a list of YouTube channels/videos. It opens each one in a real browser, reads the page the way a human QC analyst would — metadata, transcript, frames, comments, stats — and writes a validated QC record (category, brand safety, audience, language, region, engagement) to CSV / Excel.

🧠 2 LLM calls/video · 🌐 real-browser extraction · 🛡️ deterministic safety validator · 📊 CSV + styled Excel

---

## 🚀 Quick start

🔑 **Access = install.** All you need is to be granted access to this Bitbucket repo. The install authorizes through your browser with your existing Bitbucket login (via Git Credential Manager) — no SSH key, no password to type. See [New Mac — step by step](#-new-mac--step-by-step-repo-access-only) below.

<details>
<summary>🆕 One-command bootstrap (requires hosting first)</summary>

> ⚠️ **`$BASE` is not yet a real URL.** Running the command below as-is will fail with *"No host part in the URL."* This section only works after a teammate hosts the script at a real internal URL. Until then, **use the step-by-step guide below instead.**

One command installs **everything** — Homebrew, Python, git, Google Chrome, `ytqc` — then runs setup. But `$BASE` must be a **real internal URL** a teammate has uploaded the script to (the private Bitbucket repo can't serve it).

```bash
# ONLY once $BASE is a real hosted URL:
curl -fsSL "$BASE/bootstrap.sh" | bash               # macOS
irm "$BASE/bootstrap.ps1" | iex                      # Windows (PowerShell)
```

</details>

---

## 💻 Already have Python 3.10+ & git

```bash
python3 -m pip install --user pipx && python3 -m pipx ensurepath
source ~/.zshrc   # or close & reopen Terminal
pipx install "git+https://bitbucket.org/silverpush/yt-qc-agent.git"   # browser-authorizes via your Bitbucket access
ytqc setup        # wizard runs; when it pauses for ollama signin, complete it:
ollama signin     # opens browser → sign in → return to terminal, then press Enter
```

---

## 🍎 New Mac — step by step (repo access only)

If you've been granted access to the repo, this is all you need — no SSH key, no app password. Git Credential Manager opens a browser once; you click Authorize with your Bitbucket login, and you're in.

### 1 — Install Homebrew, tooling, and Git Credential Manager

```bash
# Homebrew (skip if `brew` already works)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
eval "$(/opt/homebrew/bin/brew shellenv)"          # Apple Silicon; Intel: /usr/local/bin

brew install pipx git
brew install --cask git-credential-manager         # browser-based Bitbucket auth (no key/password)
git-credential-manager configure
pipx ensurepath
source ~/.zshrc   # or close & reopen Terminal
```

### 2 — Install ytqc (a browser opens once → click Authorize)

```bash
pipx install "git+https://bitbucket.org/silverpush/yt-qc-agent.git"
```

The first pull opens your browser to authorize with Bitbucket — it uses your existing login and your repo access, so there's no key and no password to type. It's cached after that. Update later with `pipx upgrade ytqc`.

### 3 — Set up

```bash
ytqc setup        # installs Ollama + Chrome + extensions; pauses for the 3 by-hand steps
ollama signin     # run this when the wizard pauses and prompts you — opens browser to sign in
```

<details>
<summary>Fallbacks — if the browser auth isn't available</summary>

- **App password:** Bitbucket → Personal settings → App passwords → Create (tick **Repositories: Read**). Then `pipx install "git+https://bitbucket.org/silverpush/yt-qc-agent.git"` and enter your **username + app password** at the prompt (not your login password).
- **No repo access at all:** a teammate with access builds the wheel (`pip wheel . --no-deps -w dist`) and sends you `ytqc-0.1.0-py3-none-any.whl`; then `pipx install ./ytqc-0.1.0-py3-none-any.whl` — no Bitbucket login needed.

</details>

---

## ⏸️ The 3 by-hand steps (happen inside `ytqc setup`)

The wizard runs on its own, pauses to prompt you for each, then re-checks and finishes once you're done. They are not separate steps you run yourself.

| Step | When the wizard prompts you | Why |
|------|-----------------------------|-----|
| 1️⃣ | `ollama signin` | the default cloud model is tied to your free Ollama account |
| 2️⃣ | Restart Chrome once | so the auto-installed extensions load |
| 3️⃣ | Sign into YouTube in Chrome | QC opens real pages. Use a dedicated account — YouTube Premium skips ~20s of ad waits per video. |

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
<summary>📦 Install options (pip · HTTPS · dev) & updating</summary>

Default is HTTPS — with Git Credential Manager it authorizes in your browser using your Bitbucket access (no key/password). SSH also works if you prefer keys.

```bash
# Recommended — pipx (isolated), HTTPS + browser auth:
pipx install "git+https://bitbucket.org/silverpush/yt-qc-agent.git"

# Plain pip (into the current Python / a venv):
pip install "git+https://bitbucket.org/silverpush/yt-qc-agent.git"

# SSH instead (if you use keys):
pip install "git+ssh://git@bitbucket.org/silverpush/yt-qc-agent.git"

# From a local checkout (development):
pip install -e .
```

**Updating:** `pipx upgrade ytqc`  (or `pipx reinstall ytqc`); for plain pip, re-install with `--force-reinstall`.

</details>

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
pip install -e ".[dev]"
pytest tests/            # 297 tests — validator XOR matrix, sampler math, safety gates,
                         # JSON salvage, channel aggregation, setup/anti-hang robustness
```

</details>
