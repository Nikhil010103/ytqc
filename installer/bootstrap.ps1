<#
  ytqc — zero-to-running bootstrap (Windows / PowerShell).

  For a machine with NOTHING installed. Run in PowerShell:
    irm "$BASE/bootstrap.ps1" | iex
  (or: powershell -ExecutionPolicy Bypass -File bootstrap.ps1)

  Installs: git, Python, Google Chrome (winget) -> pipx -> ytqc (from the public
  GitHub repo, no token or account needed) -> then runs `ytqc setup` (which
  installs Ollama + the model and the Chrome extensions). kimi-webbridge has no
  Windows auto-installer yet, so the wizard guides that one step.
#>
$ErrorActionPreference = 'Stop'
function Say($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Die($m) { Write-Host "error: $m" -ForegroundColor Red; exit 1 }

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
  Die "winget not found. Install 'App Installer' from the Microsoft Store, then re-run."
}

# 1. Core tools + Chrome via winget (non-zero 'already installed' exits are fine).
Say "installing git, Python, Google Chrome via winget…"
foreach ($id in @('Git.Git', 'Python.Python.3.12', 'Google.Chrome')) {
  winget install -e --id $id --accept-package-agreements --accept-source-agreements --silent
}

# Refresh PATH so the freshly-installed python/git are visible in THIS session.
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
            [Environment]::GetEnvironmentVariable('Path','User')

# 2. pipx
Say "installing pipx…"
python -m pip install --user --quiet pipx
python -m pipx ensurepath
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"

# 3. ytqc from the public GitHub repo (no token or account needed).
Say "installing ytqc…"
pipx install --force "git+https://github.com/Nikhil010103/ytqc.git"

if (-not (Get-Command ytqc -ErrorAction SilentlyContinue)) {
  Die "ytqc installed but not on PATH — open a new terminal and run: ytqc setup"
}

# 4. Setup
Say "starting ytqc setup…"
ytqc setup
