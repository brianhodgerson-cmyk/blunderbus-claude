param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$LogDir     = Join-Path $ProjectDir "logs"
$LogFile    = Join-Path $LogDir "morning_brief.log"

function Log($msg) {
    $ts   = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Host $line
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    Add-Content -Path $LogFile -Value $line
}

Log "Starting Morning Brief Push"

# morning_brief_push.py self-bootstraps vault (reads .env for BW_MASTER_PASS,
# then calls vault.load_secrets() internally). No explicit vault unlock needed here.

$pyArgs = @(Join-Path $ScriptDir "morning_brief_push.py")
if ($Force) { $pyArgs += "--force" }

Log "Running morning_brief_push.py..."
py @pyArgs 2>&1 | Tee-Object -Append -FilePath $LogFile

Log "Done"
