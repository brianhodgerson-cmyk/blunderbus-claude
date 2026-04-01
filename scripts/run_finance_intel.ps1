param(
    [switch]$NoTelegram,
    [switch]$NoNarrative,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$LogDir     = Join-Path $ProjectDir "logs"
$LogFile    = Join-Path $LogDir "finance_intel.log"

function Log($msg) {
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Host $line
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    Add-Content -Path $LogFile -Value $line
}

Log "Starting Finance Intelligence"

# Read BW_MASTER_PASS from .env
$envFile = Join-Path $ProjectDir ".env"
if (-not (Test-Path $envFile)) { Log "ERROR: .env not found"; exit 1 }

$BW_MASTER_PASS = $null
foreach ($line in Get-Content $envFile) {
    if ($line -match '^BW_MASTER_PASS\s*=\s*(.+)$') {
        $BW_MASTER_PASS = $Matches[1].Trim('"').Trim("'")
    }
}
if (-not $BW_MASTER_PASS) { Log "ERROR: BW_MASTER_PASS not in .env"; exit 1 }

# Unlock vault
$bw = (Get-Command bw -ErrorAction SilentlyContinue).Source
if (-not $bw) {
    $bw = "C:\Users\brian\AppData\Local\Microsoft\WinGet\Packages\Bitwarden.CLI_Microsoft.Winget.Source_8wekyb3d8bbwe\bw.exe"
}
Log "Unlocking vault..."
# Suppress non-fatal bw warnings with SilentlyContinue
$ErrorActionPreference = "SilentlyContinue"
& $bw config server "https://vaultwarden.hodgespot.com" 2>$null | Out-Null
$BW_SESSION = & $bw unlock $BW_MASTER_PASS --raw 2>$null
$ErrorActionPreference = "Stop"
if (-not $BW_SESSION) { Log "ERROR: Vault unlock failed"; exit 1 }
Log "Vault unlocked"

# Pull secrets - no Anthropic API key needed (narrative uses `claude` CLI)
$telegramItem       = (& $bw get item "telegram-bot" --session $BW_SESSION 2>$null) | ConvertFrom-Json
$TELEGRAM_BOT_TOKEN = ($telegramItem.fields | Where-Object { $_.name -eq "token" }).value
$TELEGRAM_CHAT_ID   = ($telegramItem.fields | Where-Object { $_.name -eq "chat_id" }).value

$obsidianItem       = (& $bw get item "Obsidian API" --session $BW_SESSION 2>$null) | ConvertFrom-Json
$OBSIDIAN_TOKEN     = ($obsidianItem.fields | Where-Object { $_.name -eq "Token" }).value

$MONARCH_TOKEN = $null
try {
    $monarchItem   = (& $bw get item "monarch" --session $BW_SESSION 2>$null) | ConvertFrom-Json -ErrorAction Stop
    $MONARCH_TOKEN = ($monarchItem.fields | Where-Object { $_.name -eq "api_token" }).value
    if ($MONARCH_TOKEN) { Log "Monarch token retrieved" } else { Log "WARN: monarch api_token field empty" }
} catch {
    Log "WARN: Could not fetch 'monarch' from vault — ingest will be skipped"
}

Log "Secrets retrieved"

# Start Obsidian if not running
$obsProc = Get-Process -Name "Obsidian" -ErrorAction SilentlyContinue
if (-not $obsProc) {
    Log "Starting Obsidian..."
    Start-Process "C:\Program Files\Obsidian\Obsidian.exe"
    Start-Sleep -Seconds 10
}

# Open SSH tunnel on port 19001 (kill any stale binding first)
$stale = Get-NetTCPConnection -LocalPort 19001 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($stale) {
    Stop-Process -Id $stale.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}
Log "Opening SSH tunnel..."
Start-Process -FilePath "ssh" `
    -ArgumentList "-o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -fNL 19001:172.18.0.4:9000 cortex" `
    -WindowStyle Hidden

# Poll until port 19001 is accepting connections (max 20s)
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

# Set env vars and run script
$env:OBSIDIAN_TOKEN      = $OBSIDIAN_TOKEN
$env:TELEGRAM_BOT_TOKEN  = $TELEGRAM_BOT_TOKEN
$env:TELEGRAM_CHAT_ID    = $TELEGRAM_CHAT_ID
$env:CLICKHOUSE_HOST     = "127.0.0.1"
$env:CLICKHOUSE_PORT     = "19001"
$env:CLICKHOUSE_PASSWORD = "clickhouse"
$env:OBSIDIAN_URL        = "https://127.0.0.1:27124"
$env:MONARCH_TOKEN       = $MONARCH_TOKEN

# Run Monarch Money ingest first to refresh ClickHouse with today's data
if ($MONARCH_TOKEN) {
    Log "Running monarch_ingest.py (pulling fresh account + transaction data)..."
    py (Join-Path $ScriptDir "monarch_ingest.py") 2>&1 | Tee-Object -Append -FilePath $LogFile
    Log "Monarch ingest complete"
} else {
    Log "WARN: Skipping Monarch ingest — no token available"
}

$pyArgs = @(Join-Path $ScriptDir "finance_intel.py")
if ($NoTelegram)  { $pyArgs += "--no-telegram" }
if ($NoNarrative) { $pyArgs += "--no-narrative" }
if ($DryRun)      { $pyArgs += "--dry-run" }

Log "Running finance_intel.py..."
py @pyArgs 2>&1 | Tee-Object -Append -FilePath $LogFile

# Cleanup tunnel
$conn = Get-NetTCPConnection -LocalPort 19001 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($conn) { Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue }

# Clear secrets
"OBSIDIAN_TOKEN","TELEGRAM_BOT_TOKEN","TELEGRAM_CHAT_ID" | ForEach-Object {
    Remove-Item "Env:\$_" -ErrorAction SilentlyContinue
}
$BW_SESSION = $null; $BW_MASTER_PASS = $null

Log "Done"
