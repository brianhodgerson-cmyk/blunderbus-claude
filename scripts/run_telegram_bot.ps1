param(
    [switch]$NoAllowlist   # Skip user ID check (for local testing only)
)

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$LogDir     = Join-Path $ProjectDir "logs"
$LogFile    = Join-Path $LogDir "telegram_bot.log"

function Log($msg) {
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Host $line
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    Add-Content -Path $LogFile -Value $line
}

Log "Starting BlunderBus Telegram bot"

# ── Load .env ──────────────────────────────────────────────────────────────────
$envFile = Join-Path $ProjectDir ".env"
if (-not (Test-Path $envFile)) { Log "ERROR: .env not found at $envFile"; exit 1 }

$ALLOWED_USER_IDS = $null
$BW_MASTER_PASS   = $null

foreach ($line in Get-Content $envFile) {
    if ($line -match '^TELEGRAM_ALLOWED_USER_IDS\s*=\s*(.+)$') {
        $ALLOWED_USER_IDS = $Matches[1].Trim('"').Trim("'")
    }
    if ($line -match '^BW_MASTER_PASS\s*=\s*(.+)$') {
        $BW_MASTER_PASS = $Matches[1].Trim('"').Trim("'")
    }
}

if (-not $BW_MASTER_PASS) { Log "ERROR: BW_MASTER_PASS not in .env"; exit 1 }

# ── Unlock vault ───────────────────────────────────────────────────────────────
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

# ── Pull secrets ───────────────────────────────────────────────────────────────
$telegramItem       = (& $bw get item "telegram-bot" --session $BW_SESSION 2>$null) | ConvertFrom-Json
$TELEGRAM_BOT_TOKEN = ($telegramItem.fields | Where-Object { $_.name -eq "token" }).value

if (-not $TELEGRAM_BOT_TOKEN) { Log "ERROR: telegram-bot token not found in vault"; exit 1 }
Log "Secrets retrieved"

# ── Set env vars ───────────────────────────────────────────────────────────────
$env:TELEGRAM_BOT_TOKEN = $TELEGRAM_BOT_TOKEN

if ($ALLOWED_USER_IDS -and -not $NoAllowlist) {
    $env:TELEGRAM_ALLOWED_USER_IDS = $ALLOWED_USER_IDS
    Log "Allowlist: $ALLOWED_USER_IDS"
} else {
    Log "WARNING: No TELEGRAM_ALLOWED_USER_IDS set - bot responds to anyone"
}

# ── Clear sensitive vars ───────────────────────────────────────────────────────
$BW_SESSION = $null; $BW_MASTER_PASS = $null

# ── Run the bot ────────────────────────────────────────────────────────────────
$BotScript = Join-Path $ScriptDir "telegram_bot.py"
Log "Launching telegram_bot.py..."

try {
    python $BotScript 2>&1 | Tee-Object -Append -FilePath $LogFile
} finally {
    Remove-Item "Env:\TELEGRAM_BOT_TOKEN" -ErrorAction SilentlyContinue
    Remove-Item "Env:\TELEGRAM_ALLOWED_USER_IDS" -ErrorAction SilentlyContinue
    Log "Bot stopped. Secrets cleared."
}
