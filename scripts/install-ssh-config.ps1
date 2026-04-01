param(
  [string]$CortexUser = "root",
  [string]$StarkUser = "blunderbus",
  [string]$DefaultUser = "brian",
  [string]$LxcUser = "root",
  [switch]$Force
)

$sshDir = Join-Path $HOME ".ssh"
$configPath = Join-Path $sshDir "config"
$blockStart = "# >>> blunderbus-claude >>>"
$blockEnd = "# <<< blunderbus-claude <<<"

# SSH host aliases for the HodgeSpot cluster.
# No credentials here - authentication uses the key at ~/.ssh/id_ed25519.
# Users: root on Proxmox/Cortex/LXC containers, blunderbus on Stark, brian on Thor/Fury, truenas_admin on TrueNAS.

$configBlock = @"
$blockStart
# --- Proxmox Host ---
Host proxmox multiverse
  HostName 192.168.50.100
  User root
  ConnectTimeout 5
  StrictHostKeyChecking accept-new
  PreferredAuthentications publickey

# --- QEMU VMs ---
Host cortex
  HostName 192.168.50.106
  User $CortexUser
  ProxyJump stark
  ConnectTimeout 5
  StrictHostKeyChecking accept-new
  PreferredAuthentications publickey

Host stark
  HostName 192.168.50.204
  User $StarkUser
  ConnectTimeout 5
  StrictHostKeyChecking accept-new
  PreferredAuthentications publickey

Host thor
  HostName 192.168.50.136
  User $DefaultUser
  ConnectTimeout 5
  StrictHostKeyChecking accept-new
  PreferredAuthentications publickey

Host truenas heimdall
  HostName 192.168.50.50
  User truenas_admin
  ConnectTimeout 5
  StrictHostKeyChecking accept-new
  PreferredAuthentications publickey

Host homeassistant
  HostName 192.168.50.206
  User root
  ConnectTimeout 5
  StrictHostKeyChecking accept-new
  PreferredAuthentications publickey

Host fury
  HostName 192.168.50.103
  User $DefaultUser
  ConnectTimeout 5
  StrictHostKeyChecking accept-new
  PreferredAuthentications publickey

# --- LXC Containers (root only - minimal Debian, no non-root users) ---
Host banner
  HostName 192.168.50.202
  User $LxcUser
  ConnectTimeout 5
  StrictHostKeyChecking accept-new
  PreferredAuthentications publickey

Host groot
  HostName 192.168.50.53
  User $LxcUser
  ConnectTimeout 5
  StrictHostKeyChecking accept-new
  PreferredAuthentications publickey

Host loki
  HostName 192.168.50.207
  User $LxcUser
  ConnectTimeout 5
  StrictHostKeyChecking accept-new
  PreferredAuthentications publickey

Host ultron
  HostName 192.168.50.209
  User $LxcUser
  ConnectTimeout 5
  StrictHostKeyChecking accept-new
  PreferredAuthentications publickey

Host vision
  HostName 192.168.50.210
  User $LxcUser
  ConnectTimeout 5
  StrictHostKeyChecking accept-new
  PreferredAuthentications publickey

Host hawkeye-nvr
  HostName 192.168.50.205
  User $LxcUser
  ConnectTimeout 5
  StrictHostKeyChecking accept-new
  PreferredAuthentications publickey
$blockEnd
"@

New-Item -ItemType Directory -Force -Path $sshDir | Out-Null

if (Test-Path $configPath) {
  $existing = Get-Content -Path $configPath -Raw
  if ($existing.Contains($blockStart)) {
    if (-not $Force) {
      throw "BlunderBus SSH aliases already exist in $configPath. Re-run with -Force to replace them."
    }

    $pattern = "(?s)$([regex]::Escape($blockStart)).*?$([regex]::Escape($blockEnd))\r?\n?"
    $updated = [regex]::Replace($existing, $pattern, $configBlock.TrimEnd() + [Environment]::NewLine)
    Set-Content -Path $configPath -Value $updated
    Write-Host "Updated BlunderBus SSH aliases in $configPath"
    exit 0
  }

  $prefix = ""
  if ($existing.Length -gt 0 -and -not $existing.EndsWith("`n")) {
    $prefix = [Environment]::NewLine
  }

  Add-Content -Path $configPath -Value ($prefix + $configBlock.TrimEnd() + [Environment]::NewLine)
  Write-Host "Appended BlunderBus SSH aliases to $configPath"
  exit 0
}

Set-Content -Path $configPath -Value ($configBlock.TrimEnd() + [Environment]::NewLine)
Write-Host "Created $configPath with BlunderBus SSH aliases"
