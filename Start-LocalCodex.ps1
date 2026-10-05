#Requires -Version 7.0
<#
.SYNOPSIS
    Initialisiert und startet die OpenAI Codex CLI gegen das lokale Gateway
    http://localhost:20128/v1 (Dashboard: /dashboard/cli-tools/codex).

.DESCRIPTION
    Doppelklick-Starter fuer Codex mit lokalem Gateway:

      1. prueft, ob das Gateway auf Port 20128 antwortet und ob das gewuenschte
         Modell angeboten wird
      2. prueft die Abhaengigkeiten (node, npm, codex) und installiert fehlende
         automatisch nach
      3. initialisiert den Codex-Provider in %USERPROFILE%\.codex\config.toml -
         nur wenn er fehlt; eine vorhandene, funktionierende Konfiguration wird
         unveraendert gelassen (vorher wird immer eine .bak angelegt)
      4. startet Codex: standardmaessig die interaktive TUI mit --no-daemon

    Das Skript legt Dateien an (Arbeitsverzeichnis, Logs, ggf. config.toml) und
    fuehrt Code aus (es startet Codex bzw. codex exec). Codex laeuft mit
    Sandbox `workspace-write`, kann also im Arbeitsverzeichnis Dateien schreiben
    und Shell-Kommandos ausfuehren; mit -FullAccess ohne Sandbox.

    API-/Token-Warnungen werden bewusst unterdrueckt und nicht ausgegeben.

.PARAMETER Endpoint
    OpenAI-kompatible Gateway-Wurzel inkl. /v1. Default: http://localhost:20128/v1

.PARAMETER Token
    Bearer-Token fuer die Initialisierung. Reihenfolge: Parameter ->
    $env:LOCALCODEX_API_KEY -> eingebauter Default.

.PARAMETER Model
    Modell-id. Default: gpt-6-astra

.PARAMETER ProviderId
    Provider-Schluessel in config.toml. Default: flagshiprouter

.PARAMETER WorkDir
    Arbeitswurzel fuer Codex. Default: C:\CodexAstra

.PARAMETER Prompt
    One-Shot-Prompt statt TUI (impliziert -OneShot).

.PARAMETER OneShot
    Startet `codex exec` einmalig statt der TUI.

.PARAMETER SelfTest
    Selbsttest: Codex legt eine Datei an, liest sie per Shell zurueck und meldet
    SELFTEST_DONE; das Ergebnis wird verifiziert.

.PARAMETER List
    Listet nur die Modelle des Gateways auf und beendet.

.PARAMETER FullAccess
    Hebt die Sandbox auf (--dangerously-bypass-approvals-and-sandbox).

.PARAMETER WithDaemon
    Startet die TUI mit dem geteilten Hintergrund-Server (ohne --no-daemon).

.PARAMETER NoConfigWrite
    Fasst config.toml nicht an - nur pruefen, nichts schreiben.

.EXAMPLE
    pwsh -File .\Start-LocalCodex.ps1
.EXAMPLE
    pwsh -File .\Start-LocalCodex.ps1 -SelfTest
.EXAMPLE
    pwsh -File .\Start-LocalCodex.ps1 -Prompt 'Lege hallo.txt mit "hi" an und zeige sie.'
.EXAMPLE
    pwsh -File .\Start-LocalCodex.ps1 -List
#>
[CmdletBinding()]
param(
    [string]$Endpoint = 'http://localhost:20128/v1',
    [string]$Dashboard = 'http://localhost:20128/dashboard/cli-tools/codex',
    [string]$Token,
    [string]$Model = 'gpt-6-astra',
    [string]$ProviderId = 'flagshiprouter',
    [string]$WorkDir = 'C:\CodexAstra',
    [string]$Prompt,
    [switch]$OneShot,
    [switch]$SelfTest,
    [switch]$List,
    [switch]$FullAccess,
    [switch]$WithDaemon,
    [switch]$NoConfigWrite
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
# API-/Token-Warnungen sollen nicht erscheinen.
$WarningPreference = 'SilentlyContinue'
$ProgressPreference = 'SilentlyContinue'

$KeyVar = 'LOCALCODEX_API_KEY'
$SelftestFile = 'codex-selftest.txt'
$SelftestMarker = 'SELFTEST_DONE'

function Write-Step { param([string]$Text) Write-Host "`n== $Text" -ForegroundColor Cyan }
function Write-Ok { param([string]$Text) Write-Host "   OK   $Text" -ForegroundColor Green }
function Write-Bad { param([string]$Text) Write-Host "   STOP $Text" -ForegroundColor Red }
function Write-Info { param([string]$Text) Write-Host "        $Text" -ForegroundColor Gray }
function Stop-Script {
    param([string]$Text)
    Write-Bad $Text
    if (-not [Console]::IsInputRedirected) { Read-Host 'Enter zum Schliessen' | Out-Null }
    exit 1
}

# ---------------------------------------------------------------------------
# 1) Arbeitsverzeichnis anlegen
# ---------------------------------------------------------------------------
Write-Step 'Arbeitsverzeichnis vorbereiten'
if (-not (Test-Path -LiteralPath $WorkDir)) {
    New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null
}
$WorkDir = (Resolve-Path -LiteralPath $WorkDir).Path
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Write-Ok "WorkDir = $WorkDir"

# ---------------------------------------------------------------------------
# 2) Token aufloesen
# ---------------------------------------------------------------------------
Write-Step 'Token aufloesen'
$key = if (-not [string]::IsNullOrWhiteSpace($Token)) { $Token.Trim() }
elseif (-not [string]::IsNullOrWhiteSpace($env:LOCALCODEX_API_KEY)) { $env:LOCALCODEX_API_KEY.Trim() }
else {
    # Fallback: Token aus der lokalen (gitignored) config.json lesen
    $cfgTok = $null
    $cfgPath = Join-Path $PSScriptRoot 'config.json'
    if (Test-Path -LiteralPath $cfgPath) {
        try { $cfgTok = (Get-Content -Raw -LiteralPath $cfgPath | ConvertFrom-Json).token } catch { }
    }
    if (-not [string]::IsNullOrWhiteSpace($cfgTok)) { $cfgTok.Trim() } else { 'DEIN_TOKEN_HIER' }
}
$env:LOCALCODEX_API_KEY = $key
Write-Ok ("Key endet auf ...{0}" -f $key.Substring([Math]::Max(0, $key.Length - 4)))

# ---------------------------------------------------------------------------
# 3) Gateway pruefen (GET /models kostet keine Credits)
# ---------------------------------------------------------------------------
Write-Step "Gateway pruefen: $Endpoint"
function Get-GatewayModels {
    param([string]$Base, [string]$Tok)
    try {
        $resp = Invoke-RestMethod -Uri "$Base/models" -Method Get -TimeoutSec 15 `
            -Headers @{ Authorization = "Bearer $Tok" } -WarningAction SilentlyContinue
        return @($resp.data | ForEach-Object { $_.id })
    }
    catch { return $null }
}
$modelIds = Get-GatewayModels -Base $Endpoint -Tok $key
if (-not $modelIds) {
    Stop-Script "Gateway $Endpoint nicht erreichbar. Laeuft der Dienst auf Port 20128? Dashboard: $Dashboard"
}
Write-Ok ("{0} Modelle am Gateway" -f $modelIds.Count)

if ($List) {
    Write-Step "Modelle an $Endpoint"
    $modelIds | Sort-Object | ForEach-Object { Write-Host "   $_" }
    if (-not [Console]::IsInputRedirected) { Read-Host 'Enter zum Schliessen' | Out-Null }
    exit 0
}

if ($modelIds -notcontains $Model) {
    $near = @($modelIds | Where-Object { $_ -like '*astra*' -or $_ -like 'gpt-*' } | Select-Object -First 12)
    Stop-Script ("Modell '$Model' fehlt am Gateway. Kandidaten: " + ($near -join ', '))
}
Write-Ok "Modell = $Model"

# ---------------------------------------------------------------------------
# 4) Abhaengigkeiten pruefen und automatisch nachinstallieren
# ---------------------------------------------------------------------------
Write-Step 'Abhaengigkeiten pruefen'
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Info 'node fehlt - installiere Node.js LTS via winget ...'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        & winget install --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements --silent | Out-Null
    }
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
        Stop-Script 'node ist nicht verfuegbar. Bitte Node.js installieren und erneut starten.'
    }
}
Write-Ok ("node " + (& node --version))

$codexCmd = Get-Command codex -ErrorAction SilentlyContinue
if (-not $codexCmd) {
    Write-Info 'codex fehlt - installiere @openai/codex global via npm ...'
    & npm install -g '@openai/codex' | Out-Null
    $codexCmd = Get-Command codex -ErrorAction SilentlyContinue
    if (-not $codexCmd) { Stop-Script 'codex installiert, aber nicht auf PATH. Terminal neu oeffnen und erneut starten.' }
}
$codexVersion = (& codex --version 2>$null | Select-Object -First 1)
Write-Ok "$codexVersion -> $($codexCmd.Source)"

# Echtes CODEX_HOME benutzen - nie kopieren, die Hook-Trust-Hashes haengen am Pfad.
$codexHome = Join-Path $env:USERPROFILE '.codex'
if (Test-Path -LiteralPath $codexHome) {
    $env:CODEX_HOME = $codexHome
}

# ---------------------------------------------------------------------------
# 5) config.toml initialisieren (nur wenn noetig, immer mit Backup)
# ---------------------------------------------------------------------------
$configPath = Join-Path $codexHome 'config.toml'
Write-Step "Codex-Provider initialisieren ($configPath)"

function New-ProviderBlock {
    param([string]$Id, [string]$Base, [string]$Tok, [string]$Mdl)
    @"
model = "$Mdl"
model_provider = "$Id"

[model_providers.$Id]
name = "Local Codex Gateway"
base_url = "$Base"
wire_api = "responses"

[model_providers.$Id.http_headers]
Authorization = "Bearer $Tok"
"@
}

if ($NoConfigWrite) {
    Write-Info 'NoConfigWrite gesetzt - config.toml wird nicht angefasst.'
}
elseif (-not (Test-Path -LiteralPath $configPath)) {
    New-Item -ItemType Directory -Path $codexHome -Force | Out-Null
    New-ProviderBlock -Id $ProviderId -Base $Endpoint -Tok $key -Mdl $Model |
        Set-Content -LiteralPath $configPath -Encoding UTF8
    Write-Ok "config.toml neu angelegt mit Provider '$ProviderId'."
}
else {
    $lines = @(Get-Content -LiteralPath $configPath)
    $hasBlock = [bool]($lines | Where-Object { $_ -match ("^\[model_providers\." + [regex]::Escape($ProviderId) + "\]\s*$") })
    $activeProvider = ($lines | Where-Object { $_ -match '^\s*model_provider\s*=' } | Select-Object -First 1)

    if ($hasBlock) {
        Write-Ok "Provider '$ProviderId' ist bereits konfiguriert - bleibt unveraendert."
        if ($activeProvider) { Write-Info ("aktiv: " + $activeProvider.Trim()) }
    }
    else {
        Copy-Item -LiteralPath $configPath -Destination "$configPath.bak-$stamp" -Force
        Write-Info "Backup: $configPath.bak-$stamp"
        $patched = foreach ($line in $lines) {
            if ($line -match '^\s*model_provider\s*=') { "model_provider = `"$ProviderId`"" } else { $line }
        }
        $patched += ''
        $patched += (New-ProviderBlock -Id $ProviderId -Base $Endpoint -Tok $key -Mdl $Model)
        $patched | Set-Content -LiteralPath $configPath -Encoding UTF8
        Write-Ok "Provider '$ProviderId' ergaenzt."
    }
}

# ---------------------------------------------------------------------------
# 6) Sandbox-Argumente
# ---------------------------------------------------------------------------
$sandboxArgs = if ($FullAccess) {
    Write-Info 'Sandbox: deaktiviert (FullAccess).'
    @('--dangerously-bypass-approvals-and-sandbox')
}
else {
    Write-Info 'Sandbox: workspace-write (Dateien anlegen + Code ausfuehren im WorkDir).'
    @('--sandbox', 'workspace-write')
}

# ---------------------------------------------------------------------------
# 7a) Modus: interaktive TUI (Standard)
# ---------------------------------------------------------------------------
$useOneShot = $OneShot -or $SelfTest -or (-not [string]::IsNullOrWhiteSpace($Prompt))

if (-not $useOneShot) {
    $daemonArgs = if ($WithDaemon) { @() } else { @('--no-daemon') }
    Write-Step "Codex TUI starten (Modell $Model)"
    Write-Info ("Befehl: codex " + (($daemonArgs + @('-m', $Model, '-C', $WorkDir)) -join ' '))
    Write-Info 'Beenden mit /quit oder Ctrl+C.'
    Push-Location $WorkDir
    try {
        & codex @daemonArgs -m $Model -C $WorkDir @sandboxArgs
    }
    finally { Pop-Location }
    if (-not [Console]::IsInputRedirected) { Read-Host 'Fertig - Enter zum Schliessen' | Out-Null }
    exit 0
}

# ---------------------------------------------------------------------------
# 7b) Modus: One-Shot (Selbsttest oder eigener Prompt)
# ---------------------------------------------------------------------------
if ($SelfTest -or [string]::IsNullOrWhiteSpace($Prompt)) {
    $Prompt = "Lege im aktuellen Verzeichnis die Datei '$SelftestFile' mit dem Inhalt 'codex-local-gateway-ok' an. " +
    "Fuehre danach ein Shell-Kommando aus, das die Datei wieder einliest und ihren Inhalt ausgibt. " +
    "Antworte am Ende mit genau einer Zeile: $SelftestMarker"
}

$logFile = Join-Path $WorkDir "codex-run-$stamp.log"
$lastMsgFile = Join-Path $WorkDir "codex-last-$stamp.txt"
$emptyStdin = Join-Path ([System.IO.Path]::GetTempPath()) 'codex-empty-stdin.txt'
[System.IO.File]::WriteAllText($emptyStdin, '')

Write-Step "One-Shot gegen $Model"
Write-Info "Prompt: $Prompt"

$output = $null
$rc = 1
Push-Location $WorkDir
try {
    # Leere stdin-Datei: sonst liest `codex exec` die Konsole als Prompt-Anhang.
    $output = Get-Content -Raw $emptyStdin | & codex exec --skip-git-repo-check `
        -C $WorkDir `
        -m $Model `
        @sandboxArgs `
        --color never `
        -o $lastMsgFile `
        $Prompt 2>&1
    $rc = $LASTEXITCODE
}
finally { Pop-Location }

$output | Set-Content -LiteralPath $logFile -Encoding UTF8
$output | ForEach-Object { Write-Host $_ }

# ---------------------------------------------------------------------------
# 8) Verifikation: wurde wirklich eine Datei angelegt und Code ausgefuehrt?
# ---------------------------------------------------------------------------
Write-Step 'Verifikation'
$created = Join-Path $WorkDir $SelftestFile
if (Test-Path -LiteralPath $created) {
    Write-Ok "Datei angelegt: $created"
    Write-Info ("Inhalt: " + (Get-Content -LiteralPath $created -Raw).Trim())
}
else {
    Write-Info "Keine $SelftestFile gefunden (bei eigenem -Prompt normal)."
}

if (Test-Path -LiteralPath $lastMsgFile) {
    $last = (Get-Content -LiteralPath $lastMsgFile -Raw).Trim()
    if ($last -match [regex]::Escape($SelftestMarker)) {
        Write-Ok "Codex hat Code ausgefuehrt und den Marker '$SelftestMarker' gemeldet."
    }
    elseif ($last) {
        Write-Info "Letzte Codex-Antwort: $last"
    }
}

Write-Info "Log: $logFile"
Write-Host ''
if ($rc -eq 0) { Write-Ok 'Fertig.' } else { Write-Bad "codex Exit-Code $rc." }
Write-Info "Dashboard: $Dashboard"

if (-not [Console]::IsInputRedirected) {
    Write-Host ''
    Read-Host 'Fertig - Enter zum Schliessen' | Out-Null
}
