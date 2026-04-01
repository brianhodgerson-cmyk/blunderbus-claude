param(
    [switch]$DryRun,
    [switch]$NoTelegram,
    [switch]$Force,
    [string]$Date
)

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$LogDir     = Join-Path $ProjectDir "logs"
$LogFile    = Join-Path $LogDir "daily_report.log"

function Log($msg) {
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Host $line
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    Add-Content -Path $LogFile -Value $line
}

Log "Starting Daily Report"

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

# ── Unlock Vaultwarden (once) ────────────────────────────────────────────────
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
$ErrorActionPreference = "SilentlyContinue"
& $bw sync --session $BW_SESSION 2>$null | Out-Null
$ErrorActionPreference = "Stop"

# ── Pull ALL secrets (one pass) ──────────────────────────────────────────────
$OBSIDIAN_TOKEN     = $null
$TELEGRAM_BOT_TOKEN = $null
$TELEGRAM_CHAT_ID   = $null
$MONARCH_TOKEN      = $null
$SECONION_API_KEY   = $null
$TRUENAS_API_KEY    = $null

# Obsidian
try {
    $item = (& $bw get item "Obsidian API" --session $BW_SESSION 2>$null) | ConvertFrom-Json -ErrorAction Stop
    $OBSIDIAN_TOKEN = ($item.fields | Where-Object { $_.name -eq "Token" }).value
    if ($OBSIDIAN_TOKEN) { Log "  Obsidian token: OK" } else { Log "  WARN: Obsidian token field empty" }
} catch { Log "  WARN: Could not fetch 'Obsidian API'" }

# Telegram
try {
    $item = (& $bw get item "telegram-bot" --session $BW_SESSION 2>$null) | ConvertFrom-Json -ErrorAction Stop
    $TELEGRAM_BOT_TOKEN = ($item.fields | Where-Object { $_.name -eq "token" }).value
    $TELEGRAM_CHAT_ID   = ($item.fields | Where-Object { $_.name -eq "chat_id" }).value
    if ($TELEGRAM_BOT_TOKEN) { Log "  Telegram: OK" } else { Log "  WARN: Telegram token empty" }
} catch { Log "  WARN: Could not fetch 'telegram-bot'" }

# Monarch Money
try {
    $item = (& $bw get item "monarch" --session $BW_SESSION 2>$null) | ConvertFrom-Json -ErrorAction Stop
    $MONARCH_TOKEN = ($item.fields | Where-Object { $_.name -eq "api_token" }).value
    if ($MONARCH_TOKEN) { Log "  Monarch: OK" } else { Log "  WARN: Monarch api_token empty" }
} catch { Log "  WARN: Could not fetch 'monarch'" }

# SecOnion
try {
    $item = (& $bw get item "seconion-api" --session $BW_SESSION 2>$null) | ConvertFrom-Json -ErrorAction Stop
    $SECONION_API_KEY = ($item.fields | Where-Object { $_.name -eq "api_key" }).value
    if ($SECONION_API_KEY) { Log "  SecOnion: OK" } else { Log "  WARN: SecOnion api_key empty" }
} catch { Log "  WARN: Could not fetch 'seconion-api'" }

# TrueNAS
try {
    $item = (& $bw get item "truenas-api" --session $BW_SESSION 2>$null) | ConvertFrom-Json -ErrorAction Stop
    $TRUENAS_API_KEY = ($item.fields | Where-Object { $_.name -eq "api_key" }).value
    if ($TRUENAS_API_KEY) { Log "  TrueNAS: OK" } else { Log "  WARN: TrueNAS api_key empty" }
} catch { Log "  WARN: Could not fetch 'truenas-api'" }

Log "Secrets loaded"

# ── Start Obsidian if not running ─────────────────────────────────────────────
$obsProc = Get-Process -Name "Obsidian" -ErrorAction SilentlyContinue
if (-not $obsProc) {
    Log "Starting Obsidian..."
    Start-Process "C:\Program Files\Obsidian\Obsidian.exe"
    Start-Sleep -Seconds 10
}

# ── Open SSH tunnel to ClickHouse (kill stale, poll ready) ───────────────────
$stale = Get-NetTCPConnection -LocalPort 19001 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($stale) {
    Stop-Process -Id $stale.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}
Log "Opening SSH tunnel..."
Start-Process -FilePath "ssh" `
    -ArgumentList "-o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -fNL 19001:172.18.0.4:9000 cortex" `
    -WindowStyle Hidden

$tunnelReady = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Seconds 1
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect("127.0.0.1", 19001)
        $tcp.Close()
        $tunnelReady = $true
        break
    } catch { }
}
if (-not $tunnelReady) { Log "ERROR: SSH tunnel did not come up within 20s"; exit 1 }
Log "Tunnel up on localhost:19001"

# ── Set env vars ─────────────────────────────────────────────────────────────
$env:OBSIDIAN_TOKEN      = $OBSIDIAN_TOKEN
$env:OBSIDIAN_URL        = "https://127.0.0.1:27124"
$env:TELEGRAM_BOT_TOKEN  = $TELEGRAM_BOT_TOKEN
$env:TELEGRAM_CHAT_ID    = $TELEGRAM_CHAT_ID
$env:CLICKHOUSE_HOST     = "127.0.0.1"
$env:CLICKHOUSE_PORT     = "19001"
$env:CLICKHOUSE_PASSWORD = "clickhouse"
$env:MONARCH_TOKEN       = $MONARCH_TOKEN
$env:SECONION_API_KEY    = $SECONION_API_KEY
$env:TRUENAS_API_KEY     = $TRUENAS_API_KEY

# ── Run orchestrator ─────────────────────────────────────────────────────────
$pyArgs = @((Join-Path $ScriptDir "daily_report.py"))
if ($DryRun)      { $pyArgs += "--dry-run" }
if ($NoTelegram)  { $pyArgs += "--no-telegram" }
if ($Force)       { $pyArgs += "--force" }
if ($Date)        { $pyArgs += "--date"; $pyArgs += $Date }

Log "Running daily_report.py..."
py @pyArgs 2>&1 | Tee-Object -Append -FilePath $LogFile

# ── Cleanup ──────────────────────────────────────────────────────────────────
# Kill SSH tunnel
$conn = Get-NetTCPConnection -LocalPort 19001 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn) { Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue }

# Clear secrets from env
"OBSIDIAN_TOKEN","TELEGRAM_BOT_TOKEN","TELEGRAM_CHAT_ID","CLICKHOUSE_HOST",
"CLICKHOUSE_PORT","CLICKHOUSE_PASSWORD","MONARCH_TOKEN","SECONION_API_KEY",
"TRUENAS_API_KEY" | ForEach-Object {
    Remove-Item "Env:\$_" -ErrorAction SilentlyContinue
}
$BW_SESSION = $null; $BW_MASTER_PASS = $null

Log "Done"
