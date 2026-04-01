$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$LogDir     = Join-Path $ProjectDir "logs"
$LogFile    = Join-Path $LogDir "telegram_channels.log"

function Log($msg) {
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Host $line
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    Add-Content -Path $LogFile -Value $line
}

$ClaudePath = "C:\Users\brian\AppData\Roaming\npm\claude.cmd"
if (-not (Test-Path $ClaudePath)) {
    Log "ERROR: claude.cmd not found at $ClaudePath"
    exit 1
}

$MaxRetries = 10
$RetryDelay = 30  # seconds between restarts
$attempt = 0

Log "Starting BlunderBus Telegram Channels bridge"
Log "Working directory: $ProjectDir"

while ($attempt -lt $MaxRetries) {
    $attempt++
    Log "Launching claude (attempt $attempt/$MaxRetries)"

    try {
        Push-Location $ProjectDir
        & $ClaudePath --dangerously-skip-permissions
        $exitCode = $LASTEXITCODE
        Log "Claude exited with code $exitCode"
    } catch {
        Log "ERROR: $($_.Exception.Message)"
    } finally {
        Pop-Location
    }

    if ($attempt -lt $MaxRetries) {
        Log "Restarting in $RetryDelay seconds..."
        Start-Sleep -Seconds $RetryDelay
    }
}

Log "Max retries ($MaxRetries) reached. Stopping."
