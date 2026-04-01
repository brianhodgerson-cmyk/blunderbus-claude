param()

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$LogDir     = Join-Path $ProjectDir "logs"
$LogFile    = Join-Path $LogDir "morning_prep.log"

function Log($msg) {
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Host $line
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    Add-Content -Path $LogFile -Value $line
}

Log "Starting Morning Prep"

# ── Load BW_MASTER_PASS from .env ─────────────────────────────────────────────
$envFile = Join-Path $ProjectDir ".env"
if (-not (Test-Path $envFile)) { Log "ERROR: .env not found"; exit 1 }

$BW_MASTER_PASS = $null
foreach ($line in Get-Content $envFile) {
    if ($line -match '^BW_MASTER_PASS\s*=\s*(.+)$') {
        $BW_MASTER_PASS = $Matches[1].Trim('"').Trim("'")
    }
}
if (-not $BW_MASTER_PASS) { Log "ERROR: BW_MASTER_PASS not in .env"; exit 1 }

# ── Unlock Vaultwarden ────────────────────────────────────────────────────────
$bw = (Get-Command bw -ErrorAction SilentlyContinue).Source
if (-not $bw) {
    $bw = "C:\Users\brian\AppData\Local\Microsoft\WinGet\Packages\Bitwarden.CLI_Microsoft.Winget.Source_8wekyb3d8bbwe\bw.exe"
}
Log "Unlocking vault..."
$ErrorActionPreference = "SilentlyContinue"
& $bw config server "https://vaultwarden.hodgespot.com" 2>$null | Out-Null
$BW_SESSION = & $bw unlock $BW_MASTER_PASS --raw 2>$null
$ErrorActionPreference = "Stop"
if (-not $BW_SESSION) { Log "ERROR: Vault unlock failed"; exit 1 }
Log "Vault unlocked"

# ── Sync vault ────────────────────────────────────────────────────────────────
& $bw sync --session $BW_SESSION 2>$null | Out-Null

# ── Pull secrets (non-fatal — morning_prep.py falls back to filesystem) ───────
$OBSIDIAN_TOKEN = $null
$GCAL_TOKEN     = ""

try {
    $obsidianItem   = (& $bw get item "Obsidian API" --session $BW_SESSION 2>$null) | ConvertFrom-Json -ErrorAction Stop
    $OBSIDIAN_TOKEN = ($obsidianItem.fields | Where-Object { $_.name -eq "Token" }).value
    if ($OBSIDIAN_TOKEN) { Log "Obsidian token retrieved" } else { Log "WARN: Obsidian token field empty" }
} catch {
    Log "WARN: Could not fetch 'Obsidian API' from vault - morning_prep.py will use filesystem backend"
}

try {
    $gcalItem = (& $bw get item "Google Calendar" --session $BW_SESSION 2>$null) | ConvertFrom-Json -ErrorAction Stop
    $GCAL_TOKEN = if ($gcalItem) { ($gcalItem.fields | Where-Object { $_.name -eq "token" }).value } else { "" }
    if ($GCAL_TOKEN) { Log "Google Calendar token retrieved" }
} catch {
    Log "WARN: Could not fetch 'Google Calendar' from vault - calendar features will be skipped"
}

Log "Secrets retrieved"

# ── Start Obsidian if not running ─────────────────────────────────────────────
$obsProc = Get-Process -Name "Obsidian" -ErrorAction SilentlyContinue
if (-not $obsProc) {
    Log "Starting Obsidian..."
    Start-Process "C:\Program Files\Obsidian\Obsidian.exe"
    Start-Sleep -Seconds 10
}

# ── Set env and run morning_prep.py ──────────────────────────────────────────
$env:OBSIDIAN_TOKEN = $OBSIDIAN_TOKEN
$env:OBSIDIAN_URL   = "https://127.0.0.1:27124"
if ($GCAL_TOKEN) { $env:GCAL_TOKEN = $GCAL_TOKEN }

Log "Running morning_prep.py..."
$pyScript = Join-Path $ScriptDir "morning_prep.py"
py $pyScript 2>&1 | Tee-Object -Append -FilePath $LogFile

# ── Cleanup ───────────────────────────────────────────────────────────────────
"OBSIDIAN_TOKEN","GCAL_TOKEN" | ForEach-Object {
    Remove-Item "Env:\$_" -ErrorAction SilentlyContinue
}
$BW_SESSION = $null; $BW_MASTER_PASS = $null

Log "Done"
