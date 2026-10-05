<#
    CodexAstra - Doppelklick-Starter (Windows, kein Terminal)
    Startet die Tkinter-Desktop-App ueber pythonw.exe, damit KEIN Konsolen-
    fenster erscheint. API-/Token-Warnungen werden unterdrueckt.

    Aufruf: einfach diese Datei doppelklicken. Beim allerersten Start legt das
    Skript bei Bedarf eine tkinter-faehige .venv an. Danach liegt neben dieser
    Datei zusaetzlich "CodexAstra.lnk" - ein Verknuepfung, die direkt die
    App (ganz ohne Terminal) startet.
#>
$WarningPreference = 'SilentlyContinue'
$ErrorActionPreference = 'SilentlyContinue'
$env:PYTHONWARNINGS = 'ignore'

# --- Selbst im versteckten Fenster neu starten (kein sichtbares Terminal) ---
if (-not $env:CA_HIDDEN) {
    $env:CA_HIDDEN = '1'
    $pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
    if (-not $pwsh) { $pwsh = (Get-Command powershell -ErrorAction SilentlyContinue).Source }
    if ($pwsh) {
        Start-Process -WindowStyle Hidden -FilePath $pwsh -ArgumentList @(
            '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`""
        ) | Out-Null
        exit
    }
}

$Base = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Venv = Join-Path $Base '.venv'
$PyExe  = Join-Path $Venv 'Scripts\python.exe'
$PyW    = Join-Path $Venv 'Scripts\pythonw.exe'
$App    = Join-Path $Base 'app.py'

function Test-Tk {
    param([string]$Py)
    if (-not (Test-Path $Py)) { return $false }
    try {
        & $Py -c 'import tkinter' 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

# --- .venv sicherstellen (tkinter-faehig) ---
if (-not (Test-Path $PyExe)) {
    # Kandidaten fuer einen Tk-faehigen Basis-Python (Reihenfolge = Prioritaet)
    $candidates = @(
        'C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe',
        'C:\Users\Administrator\AppData\Local\Programs\Python\Python311\pythonw.exe',
        'python', 'py'
    )
    $basePy = $null
    foreach ($c in $candidates) {
        $resolved = (Get-Command $c -ErrorAction SilentlyContinue).Source
        if ($resolved -and (Test-Tk $resolved)) { $basePy = $resolved; break }
    }
    if (-not $basePy) {
        [System.Windows.Forms.MessageBox]::Show(
            'Es wurde kein tkinter-faehiger Python-Interpreter gefunden. Bitte Python 3.11+ installieren.',
            'CodexAstra', 'OK', 'Error') 2>$null
        exit 1
    }
    & $basePy -m venv $Venv | Out-Null
}

if (-not (Test-Path $PyW)) { $PyW = $PyExe }

# --- Verknuepfung (CodexAstra.lnk) fuer sauberen, terminal-freien Start ---
$lnk = Join-Path $Base 'CodexAstra.lnk'
if (-not (Test-Path $lnk)) {
    try {
        $ws = New-Object -ComObject WScript.Shell
        $sc = $ws.CreateShortcut($lnk)
        $sc.TargetPath = $PyW
        $sc.Arguments = "`"$App`""
        $sc.WorkingDirectory = $Base
        $sc.WindowStyle = 7  # minimized
        $sc.Description = 'CodexAstra Desktop (kein Terminal)'
        $sc.Save()
    } catch { }
}

# --- App starten (pythonw = keine Konsole) ---
if (-not (Test-Path $App)) {
    [System.Windows.Forms.MessageBox]::Show("app.py nicht gefunden: $App", 'CodexAstra', 'OK', 'Error') 2>$null
    exit 1
}
Start-Process -FilePath $PyW -ArgumentList "`"$App`"" -WorkingDirectory $Base | Out-Null
