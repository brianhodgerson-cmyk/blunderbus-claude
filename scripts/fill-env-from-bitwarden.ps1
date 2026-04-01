[CmdletBinding()]
param(
    [string]$TemplatePath = '.env.example',
    [string]$OutputPath = '.env',
    [string]$MapPath = '.\scripts\bitwarden-env-map.local.psd1',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

function Get-BwExecutable {
    $command = Get-Command bw -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $fallback = Join-Path $HOME 'AppData\Local\Microsoft\WinGet\Packages\Bitwarden.CLI_Microsoft.Winget.Source_8wekyb3d8bbwe\bw.exe'
    if (Test-Path $fallback) {
        return $fallback
    }

    throw 'Bitwarden CLI was not found. Install it first.'
}

function Invoke-BwText {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $output = & $script:BwExe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Bitwarden CLI command failed: bw $($Arguments -join ' ')"
    }

    return (($output | Out-String).Trim())
}

function Invoke-BwJson {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $text = Invoke-BwText -Arguments $Arguments
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $null
    }

    return ($text | ConvertFrom-Json)
}

function Get-ObjectProperty {
    param(
        $Object,
        [string]$Name
    )

    if ($null -eq $Object) {
        return $null
    }

    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }

    return $property.Value
}

function Normalize-Label {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ''
    }

    return (($Value.ToLowerInvariant()) -replace '[^a-z0-9]', '')
}

function Get-NotesMap {
    param([string]$Notes)

    $map = @{}
    if ([string]::IsNullOrWhiteSpace($Notes)) {
        return $map
    }

    foreach ($line in ($Notes -split "\r?\n")) {
        if ($line -match '^\s*([^:=]+?)\s*[:=]\s*(.+?)\s*$') {
            $map[(Normalize-Label $matches[1])] = $matches[2].Trim()
        }
    }

    return $map
}

function Get-SingleLineNote {
    param([string]$Notes)

    if ([string]::IsNullOrWhiteSpace($Notes)) {
        return $null
    }

    $lines = @(
        $Notes -split "\r?\n" |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        ForEach-Object { $_.Trim() }
    )

    if ($lines.Count -eq 1 -and $lines[0] -notmatch '[:=]') {
        return $lines[0]
    }

    return $null
}

function Get-FieldValue {
    param(
        $Item,
        [string]$FieldName
    )

    $wanted = Normalize-Label $FieldName
    $fields = @(Get-ObjectProperty $Item 'fields')
    foreach ($field in $fields) {
        $name = Get-ObjectProperty $field 'name'
        if ((Normalize-Label $name) -eq $wanted) {
            return (Get-ObjectProperty $field 'value')
        }
    }

    return $null
}

function Get-LoginValue {
    param(
        $Item,
        [string]$Kind
    )

    $login = Get-ObjectProperty $Item 'login'
    if ($null -eq $login) {
        return $null
    }

    switch ($Kind) {
        'username' { return (Get-ObjectProperty $login 'username') }
        'password' { return (Get-ObjectProperty $login 'password') }
        'uri' {
            $uris = @(Get-ObjectProperty $login 'uris')
            foreach ($uri in $uris) {
                $value = Get-ObjectProperty $uri 'uri'
                if (-not [string]::IsNullOrWhiteSpace($value)) {
                    return $value
                }
            }
        }
    }

    return $null
}

function Get-UriText {
    param($Item)

    $login = Get-ObjectProperty $Item 'login'
    if ($null -eq $login) {
        return ''
    }

    $values = @()
    foreach ($uri in @(Get-ObjectProperty $login 'uris')) {
        $value = Get-ObjectProperty $uri 'uri'
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            $values += $value
        }
    }

    return ($values -join ' ')
}

function Test-IsSecretLike {
    param([string]$EnvName)

    return ($EnvName -match '(_PASS|_TOKEN|_KEY)$')
}

function Get-NotesValue {
    param(
        $Item,
        [string]$Key,
        [string]$EnvName
    )

    $notes = [string](Get-ObjectProperty $Item 'notes')
    $map = Get-NotesMap -Notes $notes
    $wanted = Normalize-Label $Key

    if ($map.ContainsKey($wanted)) {
        return $map[$wanted]
    }

    if ((Test-IsSecretLike $EnvName) -and ((Normalize-Label $Key) -eq (Normalize-Label $EnvName))) {
        return (Get-SingleLineNote -Notes $notes)
    }

    return $null
}

function Get-DefaultSources {
    param([string]$EnvName)

    switch -Regex ($EnvName) {
        '_USER$' { return @("login:username", "field:$EnvName", "notes:$EnvName", 'notes:username', 'notes:user') }
        '_PASS$' { return @("login:password", "field:$EnvName", "notes:$EnvName", 'notes:password', 'notes:pass') }
        '_TOKEN$' { return @("field:$EnvName", "notes:$EnvName", 'notes:token', 'notes:admin token', "login:password") }
        '(_API_KEY|_MASTER_KEY|_KEY)$' { return @("field:$EnvName", "notes:$EnvName", 'notes:api key', 'notes:key', "login:password") }
        '(_URL|_HOST)$' { return @("field:$EnvName", "notes:$EnvName", 'notes:url', 'notes:host', "login:uri") }
        default { return @("field:$EnvName", "notes:$EnvName") }
    }
}

function Get-DefaultSearchTerms {
    param([string]$EnvName)

    switch -Regex ($EnvName) {
        '^HA_' { return @('home assistant', 'homeassistant', 'jarvis') }
        '^PFSENSE_' { return @('pfsense', 'pfsense', 'router') }
        '^SECONION_' { return @('security onion', 'seconion', 'fury') }
        '^TRUENAS_' { return @('truenas', 'nas') }
        '^GRAFANA_' { return @('grafana', 'banner') }
        '^VIRUSTOTAL_' { return @('virustotal') }
        '^ABUSEIPDB_' { return @('abuseipdb') }
        '^SHODAN_' { return @('shodan') }
        '^MQTT_' { return @('mqtt', 'mosquitto') }
        '^VAULTWARDEN_' { return @('vaultwarden', 'bitwarden') }
        '^ADGUARD_' { return @('adguard') }
        '^PORTAINER_' { return @('portainer') }
        '^NPM_' { return @('nginx proxy manager', 'proxy manager', 'npm') }
        '^CLICKHOUSE_' { return @('clickhouse') }
        '^LITELLM_' { return @('litellm') }
        '^LOKI_' { return @('loki') }
        default {
            return @(
                (($EnvName -replace '_', ' ').ToLowerInvariant())
            )
        }
    }
}

function Get-DefaultRules {
    return @{
        HA_LONG_LIVED_TOKEN = @{
            ItemName = 'homeassistant-token'
            Search = @('homeassistant-token', 'home assistant', 'homeassistant', 'jarvis')
            Sources = @('field:token', 'field:HA_LONG_LIVED_TOKEN', 'notes:HA_LONG_LIVED_TOKEN', 'notes:long lived token', 'login:password')
        }
        HA_URL = @{
            ItemName = 'homeassistant-token'
            Search = @('homeassistant-token', 'home assistant', 'homeassistant')
            Sources = @('field:base_url', 'field:HA_URL', 'notes:HA_URL', 'notes:base url')
        }
        PFSENSE_USER = @{
            Search = @('pfsense', 'pfsense', 'router')
        }
        PFSENSE_PASS = @{
            Search = @('pfsense', 'pfsense', 'router')
        }
        SECONION_URL = @{
            ItemName = 'seconion-api'
            Search = @('seconion-api', 'security onion', 'seconion', 'fury')
            Sources = @('field:base_url', 'field:SECONION_URL', 'notes:SECONION_URL', 'notes:base url', 'login:uri')
        }
        SECONION_CLIENT_ID = @{
            ItemName = 'seconion-api'
            Search = @('seconion-api-client', 'seconion-api', 'security onion', 'seconion', 'fury')
            Sources = @('field:client_id', 'field:SECONION_CLIENT_ID', 'notes:SECONION_CLIENT_ID', 'notes:client id', 'notes:client_id')
        }
        SECONION_CLIENT_SECRET = @{
            ItemName = 'seconion-api'
            Search = @('seconion-api-client', 'seconion-api', 'security onion', 'seconion', 'fury')
            Sources = @('field:client_secret', 'field:SECONION_CLIENT_SECRET', 'notes:SECONION_CLIENT_SECRET', 'notes:client secret', 'notes:client_secret', 'login:password')
        }
        SECONION_TOKEN_SCOPE = @{
            ItemName = 'seconion-api'
            Search = @('seconion-api-client', 'seconion-api', 'security onion', 'seconion', 'fury')
            Sources = @('field:scope', 'field:SECONION_TOKEN_SCOPE', 'notes:SECONION_TOKEN_SCOPE', 'notes:scope')
        }
        TRUENAS_API_KEY = @{
            ItemName = 'truenas-api'
            Search = @('truenas-api', 'truenas', 'nas')
            Sources = @('field:api_key', 'field:TRUENAS_API_KEY', 'notes:TRUENAS_API_KEY', 'notes:api key', 'login:password')
        }
        GRAFANA_API_KEY = @{
            ItemName = 'grafana-api'
            Search = @('grafana-api', 'grafana', 'banner')
            Sources = @('field:api_key', 'field:GRAFANA_API_KEY', 'notes:GRAFANA_API_KEY', 'notes:api key', 'login:password')
        }
        VIRUSTOTAL_API_KEY = @{
            Search = @('virustotal')
        }
        ABUSEIPDB_API_KEY = @{
            Search = @('abuseipdb')
        }
        SHODAN_API_KEY = @{
            Search = @('shodan')
        }
        MQTT_USER = @{
            Search = @('mqtt', 'mosquitto')
        }
        MQTT_PASS = @{
            Search = @('mqtt', 'mosquitto')
        }
        VAULTWARDEN_ADMIN_TOKEN = @{
            Search = @('vaultwarden', 'bitwarden')
            Sources = @('field:VAULTWARDEN_ADMIN_TOKEN', 'notes:VAULTWARDEN_ADMIN_TOKEN', 'notes:admin token', 'login:password')
        }
        ADGUARD_USER = @{
            ItemName = 'adguard.hodgespot.com'
            Search = @('adguard.hodgespot.com', 'adguard')
            Sources = @('login:username', 'field:username', 'notes:username')
        }
        ADGUARD_PASS = @{
            ItemName = 'adguard.hodgespot.com'
            Search = @('adguard.hodgespot.com', 'adguard')
            Sources = @('login:password', 'field:password', 'notes:password')
        }
        ADGUARD_HOST = @{
            ItemName = 'adguard-api'
            Search = @('adguard-api', 'adguard')
            Sources = @('field:base_url', 'field:ADGUARD_HOST', 'notes:ADGUARD_HOST', 'notes:base url')
        }
        PORTAINER_USER = @{
            ItemName = '192.168.50.204'
            Search = @('192.168.50.204', 'portainer')
            Sources = @('login:username', 'field:username', 'notes:username')
        }
        PORTAINER_PASS = @{
            ItemName = '192.168.50.204'
            Search = @('192.168.50.204', 'portainer')
            Sources = @('login:password', 'field:password', 'notes:password')
        }
        NPM_USER = @{
            ItemName = 'npm.hodgespot.com'
            Search = @('npm.hodgespot.com', 'nginx proxy manager', 'proxy manager', 'npm')
            Sources = @('login:username', 'field:username', 'notes:username')
        }
        NPM_PASS = @{
            ItemName = 'npm.hodgespot.com'
            Search = @('npm.hodgespot.com', 'nginx proxy manager', 'proxy manager', 'npm')
            Sources = @('login:password', 'field:password', 'notes:password')
        }
        CLICKHOUSE_USER = @{
            Search = @('clickhouse')
        }
        CLICKHOUSE_PASS = @{
            Search = @('clickhouse')
        }
        LITELLM_API_KEY = @{
            Search = @('litellm')
        }
        LITELLM_MASTER_KEY = @{
            Search = @('litellm')
        }
        LOKI_URL = @{
            ItemName = 'loki-endpoint'
            Search = @('loki-endpoint', 'loki')
            Sources = @('field:base_url', 'field:LOKI_URL', 'notes:LOKI_URL', 'notes:base url')
        }
    }
}

function Merge-RuleMaps {
    param(
        [hashtable]$Base,
        [hashtable]$Override
    )

    foreach ($envName in $Override.Keys) {
        if (-not $Base.ContainsKey($envName)) {
            $Base[$envName] = @{}
        }

        foreach ($property in $Override[$envName].Keys) {
            $Base[$envName][$property] = $Override[$envName][$property]
        }
    }

    return $Base
}

function Get-LocalRuleOverrides {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return @{}
    }

    return (Import-PowerShellDataFile -Path $Path)
}

function Get-ItemScore {
    param(
        $Item,
        [string[]]$Terms,
        [string]$ItemName
    )

    $score = 0
    $name = [string](Get-ObjectProperty $Item 'name')
    $nameLower = $name.ToLowerInvariant()
    $uriLower = (Get-UriText -Item $Item).ToLowerInvariant()

    if (-not [string]::IsNullOrWhiteSpace($ItemName)) {
        $wanted = $ItemName.ToLowerInvariant()
        if ($nameLower -eq $wanted) {
            return 1000
        }
        if ($nameLower.Contains($wanted)) {
            $score += 400
        }
    }

    foreach ($term in $Terms) {
        if ([string]::IsNullOrWhiteSpace($term)) {
            continue
        }

        $termLower = $term.ToLowerInvariant()
        if ($nameLower -eq $termLower) {
            $score += 100
        }
        elseif ($nameLower.Contains($termLower)) {
            $score += 50
        }

        if ($uriLower.Contains($termLower)) {
            $score += 15
        }
    }

    return $score
}

function Get-FullItem {
    param([string]$Id)

    if ($script:FullItemCache.ContainsKey($Id)) {
        return $script:FullItemCache[$Id]
    }

    $item = Invoke-BwJson -Arguments @('--session', $script:BwSession, 'get', 'item', $Id)
    $script:FullItemCache[$Id] = $item
    return $item
}

function Resolve-ValueFromSource {
    param(
        $Item,
        [string]$EnvName,
        [string]$Source
    )

    if ($Source -match '^login:(username|password|uri)$') {
        return (Get-LoginValue -Item $Item -Kind $matches[1])
    }

    if ($Source -match '^field:(.+)$') {
        return (Get-FieldValue -Item $Item -FieldName $matches[1])
    }

    if ($Source -match '^notes:(.+)$') {
        return (Get-NotesValue -Item $Item -Key $matches[1] -EnvName $EnvName)
    }

    return $null
}

function Resolve-EnvValue {
    param(
        [string]$EnvName,
        [hashtable]$Rule,
        $Items
    )

    $itemName = $null
    if ($Rule.ContainsKey('ItemName')) {
        $itemName = [string]$Rule['ItemName']
    }

    $searchTerms = @()
    if ($Rule.ContainsKey('Search')) {
        $searchTerms = @($Rule['Search'])
    }
    else {
        $searchTerms = @(Get-DefaultSearchTerms -EnvName $EnvName)
    }

    $sources = @()
    if ($Rule.ContainsKey('Sources')) {
        $sources = @($Rule['Sources'])
    }
    else {
        $sources = @(Get-DefaultSources -EnvName $EnvName)
    }

    $ranked = @()
    foreach ($item in $Items) {
        $score = Get-ItemScore -Item $item -Terms $searchTerms -ItemName $itemName
        if ($score -gt 0) {
            $ranked += [pscustomobject]@{
                Score = $score
                Item  = $item
            }
        }
    }

    foreach ($entry in ($ranked | Sort-Object -Property @(
        @{ Expression = { $_.Score }; Descending = $true },
        @{ Expression = { Get-ObjectProperty $_.Item 'name' } }
    ))) {
        $fullItem = Get-FullItem -Id (Get-ObjectProperty $entry.Item 'id')
        foreach ($source in $sources) {
            $value = Resolve-ValueFromSource -Item $fullItem -EnvName $EnvName -Source $source
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                return [pscustomobject]@{
                    Value    = $value.Trim()
                    ItemName = [string](Get-ObjectProperty $fullItem 'name')
                    Source   = $source
                }
            }
        }
    }

    return $null
}

function Format-EnvValue {
    param([string]$Value)

    if ($Value -match '^[A-Za-z0-9_./:@%+=,-]+$') {
        return $Value
    }

    $escaped = $Value -replace "'", "'""'""'"
    return "'$escaped'"
}

function Test-IsPlaceholderValue {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }

    return (
        $Value.StartsWith('your_') -or
        $Value -match 'example\.com$' -or
        $Value -match '192\.168\.50\.x'
    )
}

$script:BwExe = Get-BwExecutable
$script:FullItemCache = @{}

if (-not (Test-Path $TemplatePath)) {
    throw "Template file not found: $TemplatePath"
}

if ((Test-Path $OutputPath) -and -not $Force) {
    throw "$OutputPath already exists. Re-run with -Force to replace it."
}

$status = Invoke-BwJson -Arguments @('status')
if ((Get-ObjectProperty $status 'status') -eq 'unauthenticated') {
    throw 'Bitwarden CLI is not logged in. Run bw config server https://vaultwarden.hodgespot.com if needed, then bw login, then re-run this script.'
}

if (-not $env:BW_SESSION) {
    $env:BW_SESSION = Invoke-BwText -Arguments @('unlock', '--raw')
}

if ([string]::IsNullOrWhiteSpace($env:BW_SESSION)) {
    throw 'No Bitwarden session is available.'
}

$script:BwSession = $env:BW_SESSION
Invoke-BwText -Arguments @('--quiet', '--session', $script:BwSession, 'sync') | Out-Null

$items = @(Invoke-BwJson -Arguments @('--session', $script:BwSession, 'list', 'items'))
$rules = Merge-RuleMaps -Base (Get-DefaultRules) -Override (Get-LocalRuleOverrides -Path $MapPath)

$filled = @()
$keptDefaults = @()
$unresolved = @()
$outputLines = New-Object System.Collections.Generic.List[string]

foreach ($line in (Get-Content -Path $TemplatePath)) {
    if ($line -notmatch '^([A-Z0-9_]+)=(.*)$') {
        [void]$outputLines.Add($line)
        continue
    }

    $envName = $matches[1]
    $currentValue = $matches[2]
    $rule = @{}
    if ($rules.ContainsKey($envName)) {
        $rule = $rules[$envName]
    }

    $resolved = Resolve-EnvValue -EnvName $envName -Rule $rule -Items $items
    if ($resolved) {
        [void]$outputLines.Add("$envName=$(Format-EnvValue -Value $resolved.Value)")
        $filled += [pscustomobject]@{
            Name   = $envName
            Item   = $resolved.ItemName
            Source = $resolved.Source
        }
        continue
    }

    [void]$outputLines.Add($line)
    if (Test-IsPlaceholderValue -Value $currentValue) {
        $unresolved += $envName
    }
    else {
        $keptDefaults += $envName
    }
}

$content = ($outputLines -join [Environment]::NewLine) + [Environment]::NewLine
$outputDirectory = Split-Path -Path $OutputPath -Parent
if (-not [string]::IsNullOrWhiteSpace($outputDirectory)) {
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
}

$resolvedOutputPath = $OutputPath
if (-not [System.IO.Path]::IsPathRooted($resolvedOutputPath)) {
    $resolvedOutputPath = Join-Path (Get-Location) $resolvedOutputPath
}

[System.IO.File]::WriteAllText($resolvedOutputPath, $content)

Write-Host "Wrote $resolvedOutputPath"

if ($filled.Count -gt 0) {
    Write-Host ''
    Write-Host 'Filled from Bitwarden:'
    foreach ($entry in ($filled | Sort-Object Name)) {
        Write-Host ("  {0} <- {1} ({2})" -f $entry.Name, $entry.Item, $entry.Source)
    }
}

if ($keptDefaults.Count -gt 0) {
    Write-Host ''
    Write-Host 'Kept template defaults:'
    foreach ($name in ($keptDefaults | Sort-Object)) {
        Write-Host ("  {0}" -f $name)
    }
}

if ($unresolved.Count -gt 0) {
    Write-Host ''
    Write-Warning ("Unresolved placeholders: {0}" -f (($unresolved | Sort-Object) -join ', '))
    if (-not (Test-Path $MapPath)) {
        Write-Host "Create $MapPath from .\\scripts\\bitwarden-env-map.example.psd1 to pin item names or note labels."
    }
}
