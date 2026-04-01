# BlunderBus - Obsidian Terminal Launcher

# Fix terminal width before any output
$width = 120
$height = 30
try {
    $rawUI = $host.UI.RawUI
    $newSize = $rawUI.BufferSize
    $newSize.Width = $width
    $rawUI.BufferSize = $newSize
    $winSize = $rawUI.WindowSize
    $winSize.Width = $width
    $winSize.Height = $height
    $rawUI.WindowSize = $winSize
} catch {}

Clear-Host

Set-Location "C:\Users\brian\Desktop\blunderbus-claude"

# Terminal type - required for Claude Code TUI to render correctly
$env:TERM = "xterm-256color"
$env:COLORTERM = "truecolor"

# ── Step 1: Load .env (non-sensitive config + BW_MASTER_PASS) ─────────────────
$envFile = "C:\Users\brian\Desktop\blunderbus-claude\.env"
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match "^\s*[A-Z_][A-Z0-9_]*\s*=" } | ForEach-Object {
        $parts = $_ -split "=", 2
        $key   = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        [System.Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
    Write-Output "  .env loaded"
}

# ── Step 2: Load secrets from Vaultwarden (overwrites .env values for API keys) ─
$vaultScript = "C:\Users\brian\Desktop\blunderbus-claude\scripts\vault.py"
if (Test-Path $vaultScript) {
    Write-Output "  Loading vault secrets..."
    $vaultLines = python $vaultScript --export 2>$null
    if ($LASTEXITCODE -eq 0 -and $vaultLines) {
        $count = 0
        $vaultLines | ForEach-Object {
            if ($_ -match "^([A-Z_][A-Z0-9_]*)=(.*)$") {
                [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
                $count++
            }
        }
        Write-Output "  Vault: $count secret(s) loaded  🔐"
    } else {
        Write-Output "  Vault: unavailable - using .env fallback"
    }
}

Write-Output ""
Write-Output "  ✅ BlunderBus workspace ready"
Write-Output "  Project : C:\Users\brian\Desktop\blunderbus-claude"
Write-Output "  Secrets : Vaultwarden (vaultwarden.hodgespot.com)"
Write-Output ""
Write-Output "  Run: claude"
Write-Output ""
