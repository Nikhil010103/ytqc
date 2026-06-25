# ytqc — Browser-Driven Agentic YouTube QC

Automates the TechOps/QC team's manual YouTube vetting: give it a CSV of
channel/video IDs and it opens each one in a **real browser** (via
kimi-webbridge), reads the page the way a human QC analyst does — metadata,
transcript, frames, comments, stats — and writes a validated QC record
(tier_1/tier_2 category, brand safety, audience, keywords, language, region,
premium flag + all engagement stats) to CSV/Excel.

## Install & set up (macOS / Windows)

`ytqc` is installed with `pip`, straight from the company's private Bitbucket repo.
Git handles the auth with your existing Bitbucket access — no tokens to paste.

**Prerequisites:** Python 3.10+ and `git` on the machine, plus access to the
Bitbucket repo. (`python3 --version` / `git --version` to check.)

**Recommended — `pipx`** (installs `ytqc` in its own isolated environment and puts
it on your PATH):

```
python3 -m pip install --user pipx && python3 -m pipx ensurepath   # one-time, if no pipx
pipx install "git+ssh://git@bitbucket.org/WORKSPACE/yt-qc-agent.git"
ytqc setup                  # one command: installs deps, connects Chrome, opens chat
```

**Plain `pip`** (into the current Python / a venv) works too:

```
pip install "git+ssh://git@bitbucket.org/WORKSPACE/yt-qc-agent.git"
# HTTPS instead of SSH:  pip install "git+https://bitbucket.org/WORKSPACE/yt-qc-agent.git"
ytqc setup
```

Replace `WORKSPACE` with the actual Bitbucket workspace/repo path. From a local
checkout, `pip install -e .` also works.

**Updating:** `pipx upgrade ytqc` (or `pipx reinstall ytqc`), or for plain pip
re-run the install with `--force-reinstall`.

`ytqc setup` is an idempotent wizard that does the heavy lifting:

1. **Ollama** — installs it (Homebrew / winget), starts the server, signs you in,
   and fetches `gemma4:31b-cloud`.
2. **kimi-webbridge** — installs and starts the browser-bridge daemon.
3. **Chrome extensions** — force-installs **kimi-webbridge** + **VidIQ** via a
   user-scope Chrome policy (no admin), so they auto-appear on next launch.
4. **Connectivity** — runs the same checks as `ytqc doctor` until everything is green.

Three steps need a human (the wizard opens/guides each): **sign into YouTube** in
Chrome (use a dedicated account; **YouTube Premium removes ~20s ad waits/video**),
**`ollama signin`** (your own account, for the cloud model), and a one-time Chrome
restart so the forced extensions load. Re-run `ytqc setup` anytime — it only fixes
what's missing. `ytqc install-launcher` adds a double-click desktop launcher
(`ytqc start`) that boots the services and opens the chat.

**Run [`ytqc guide`](#) (or `/guide` in chat) for the full in-tool setup guide** —
prerequisites, the one-command flow, and detailed how/why for each manual step.

## Requirements

The wizard installs these for you; listed for reference / manual setups:

- **kimi-webbridge** daemon + Chrome extension, with a logged-in Chrome profile
  (default `http://127.0.0.1:10086/command`). A dedicated Google account is
  recommended; **YouTube Premium removes all ad-wait time** (~20s/video otherwise).
- **Ollama** with `gemma4:31b-cloud` (default brain), or any OpenAI-compatible
  API — swap providers in `~/.ytqc/config.yaml` or with `--provider/--model`.

## How it works

```
input.csv ─► browser producer (serial, paced)          analysis workers (parallel)
             ├ player-response metadata                 ├ deterministic safety pre-gate
             ├ transcript panel scrape (60–120s         ├ Vision Analyst   (1 vision call)
             │  sampled across 5 windows)               ├ Content Analyst  (1 call — mirrors
             ├ canvas frames at window midpoints        │  taxonomy/safety/audience prompt)
             ├ likes / comments / channel stats         ├ conditional Judge (conflicts only)
             └ artifacts + JSONL checkpoint     ─────►  ├ deterministic validator (closed
                                                        │  vocab, XOR, risk floor, confidence)
                                                        └ sinks: csv / styled xlsx / es(stub)
```

- **2 LLM calls/video, ~K+1 per channel** (K sampled videos → briefs → weighted
  vote → synthesizer). Channel brand safety is worst-case across briefs, never
  averaged.
- The LLM never computes stats and never has the last word on vocabulary —
  the validator enforces the 35-value tier_1 vocab, the Kids XOR rule, and
  floors risk levels with deterministic term-gate hits.
- Measured throughput: ~80–100 items/hr mixed (browser-paced for bot hygiene);
  videos ~16s extraction + ~10s LLM, pipelined.

## Commands

| command | purpose |
|---|---|
| `ytqc setup` | one-command wizard: install deps + connect Chrome + open chat (`--repair`, `--non-interactive`) |
| `ytqc guide` | in-tool setup guide (prerequisites + the manual steps) |
| `ytqc start` | boot services (Ollama, kimi, Chrome) then open chat — the desktop launcher target |
| `ytqc install-launcher` | create a double-click desktop launcher |
| `ytqc run -i items.csv` | full QC run (`--extract-only`, `--dry-run`, `--limit`, `--no-comments`, `--no-cache`) |
| `ytqc resume <run_id> -i items.csv` | continue an interrupted run (saved artifacts reused) |
| `ytqc doctor` | connectivity + model checks |
| `ytqc configure` | write/show `~/.ytqc/config.yaml` |
| `ytqc taxonomy` | show closed vocabularies |
| `ytqc accuracy --pred results.csv --gold gold.xlsx` | per-field accuracy vs QC-team labels |

Outputs land in `./ytqc_runs/<run_id>/` — `results.csv`, styled `results.xlsx`
(green=safe, amber=needs review, red=unsafe, grey=error), `state.jsonl`,
and per-item `artifacts/` (extraction JSON for offline prompt iteration).

## Provider swap

```yaml
# ~/.ytqc/config.yaml
active_provider: ollama-cloud        # or: openai / deepseek / ollama-local
providers:
  openai: {base_url: "https://api.openai.com/v1", api_key: "${OPENAI_API_KEY}",
           model: "gpt-4o-mini", supports_vision: true}
```

Non-vision providers skip the frame analysis with a confidence penalty.

## Notes

- If YouTube serves a bot-check interstitial the run **halts and checkpoints**
  (never retries into it) — resume later.
- Captionless videos (~10–20%) degrade to frames+metadata with a confidence
  cap and a note in the QC `comment` field.
- Tests: `pytest tests/` (36 tests — validator XOR matrix, sampler math,
  safety gates, JSON salvage, channel aggregation).
