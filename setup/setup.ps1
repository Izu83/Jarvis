# Jarvis setup: puts Jarvis on this computer (your laptop, or a new PC) and starts it.
# jarvis-setup.exe runs this in a PowerShell window. Safe to run again: it skips what's already done and updates Jarvis.
#   -Check   only look: say what's installed and what would be done, change nothing
# Keep this file plain ASCII: Windows PowerShell 5.1 reads a file without a BOM in the old code page.
param([switch]$Check, [string]$Folder = "")

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # the progress bar makes downloads in Windows PowerShell 10x slower
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Repo = "https://github.com/Izu83/Jarvis.git"
$Page = "https://izu83.github.io/Jarvis/"
$TunnelUrl = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
$Steps = 10
try { $Host.UI.RawUI.WindowTitle = "Jarvis setup" } catch {}

function Step($n, $text) { Write-Host ""; Write-Host "[$n/$Steps] $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "   OK  $text" -ForegroundColor Green }
function Todo($text) { Write-Host "   ->  $text" -ForegroundColor Yellow }
function Info($text) { Write-Host "       $text" }
function Fail($text) {
    Write-Host ""; Write-Host "   !!  $text" -ForegroundColor Red
    Write-Host "       Fix that and run jarvis-setup.exe again: it carries on where it stopped."
    if (-not $Check) { Read-Host "Press Enter to close" | Out-Null }
    exit 1
}
function Ask($q) { $a = Read-Host "       $q [Y/n]"; return ($a.Trim() -eq "" -or $a.Trim() -match '^[yY]') }
function RefreshPath {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User") + ";" + (Join-Path $HOME ".local\bin")
}
function Has($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function HasPython {
    if (-not (Has "py")) { return $false }
    try { & py -3.13 -c "import sys" 2>$null | Out-Null; return $LASTEXITCODE -eq 0 } catch { return $false }
}
function Winget($id, $name) {
    if (-not (Has "winget")) { Fail "winget is missing. Update 'App Installer' in the Microsoft Store, then try again." }
    Info "Installing $name (Windows may ask you to allow it)..."
    & winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements
    RefreshPath
}

Write-Host ""
Write-Host "   J A R V I S   setup" -ForegroundColor Cyan
Write-Host "   Installs what Jarvis needs, downloads him, and connects your phone."
if ($Check) { Write-Host "   (check only: nothing is changed)" -ForegroundColor Yellow }
RefreshPath

# 1-3: the programs Jarvis runs on
Step 1 "Git"
if (Has "git") { Ok "installed" } elseif ($Check) { Todo "would install Git" } else {
    Winget "Git.Git" "Git"
    if (-not (Has "git")) { Fail "Git didn't install." }
    Ok "installed"
}

Step 2 "Python 3.13"
if (HasPython) { Ok "installed" } elseif ($Check) { Todo "would install Python 3.13" } else {
    Winget "Python.Python.3.13" "Python 3.13"
    if (-not (HasPython)) { Fail "Python 3.13 didn't install." }
    Ok "installed"
}

Step 3 "Claude Code (Jarvis's brain, on your Claude plan)"
if (Has "claude") { Ok "installed" } elseif ($Check) { Todo "would install Claude Code" } else {
    Info "Installing Claude Code..."
    Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression
    RefreshPath
    if (-not (Has "claude")) { Fail "Claude Code didn't install." }
    Ok "installed"
}

# 4: where Jarvis goes
Step 4 "Download Jarvis"
if (-not $Folder) {
    $Folder = Join-Path $HOME "Jarvis"
    if (-not $Check -and -not (Test-Path (Join-Path $Folder ".git"))) {
        Info "Jarvis goes in $Folder"
        if (-not (Ask "Is that folder OK?")) { $Folder = (Read-Host "       Folder for Jarvis").Trim('" ') }
    }
}
if (Test-Path (Join-Path $Folder ".git")) {
    if ($Check) { Ok "already in $Folder (would update it)" } else {
        Push-Location $Folder; & git pull --ff-only -q; $pulled = $LASTEXITCODE; Pop-Location
        if ($pulled -eq 0) { Ok "updated $Folder" } else { Info "Couldn't update it (local changes?); keeping it as it is." }
    }
} elseif ((Test-Path $Folder) -and (Get-ChildItem $Folder -Force | Select-Object -First 1)) {
    Fail "$Folder already has other files in it. Pick an empty folder."
} elseif ($Check) { Todo "would download Jarvis into $Folder" } else {
    & git clone -q $Repo $Folder
    if ($LASTEXITCODE -ne 0) { Fail "Couldn't download Jarvis from $Repo (internet?)." }
    Ok "downloaded to $Folder"
}
if ($Check -and -not (Test-Path $Folder)) {
    Info "The rest happens inside that folder once it's there."
    Write-Host ""; exit 0
}
Set-Location $Folder
$Py = Join-Path $Folder ".venv\Scripts\python.exe"
$Pyw = Join-Path $Folder ".venv\Scripts\pythonw.exe"

# 5: Python packages. The NVIDIA ones (about 1.5 GB) only where there's an NVIDIA card to use them.
Step 5 "Python packages (a few minutes the first time)"
$nvidia = [bool](Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" })
Info ("Graphics: " + $(if ($nvidia) { "NVIDIA, so speech runs on it (fast)" } else { "no NVIDIA card, so speech runs on the processor" }))
if ($Check) { Todo $(if (Test-Path $Py) { "would update the packages" } else { "would make .venv and install the packages" }) } else {
    if (-not (Test-Path $Py)) { & py -3.13 -m venv .venv; if ($LASTEXITCODE -ne 0) { Fail "Couldn't make the Python environment." } }
    $req = "requirements.txt"
    if (-not $nvidia) {
        $req = Join-Path $env:TEMP "jarvis-requirements.txt"
        Get-Content requirements.txt | Where-Object { $_ -notmatch '^nvidia-' } | Set-Content $req -Encoding ascii
    }
    & $Py -m pip install -q --disable-pip-version-check -r $req
    if ($LASTEXITCODE -ne 0) { Fail "Installing the Python packages failed (see above)." }
    Ok "installed"
}

# 6: the tunnel, so your phone can reach this computer from anywhere
Step 6 "Tunnel program (cloudflared)"
$Tunnel = Join-Path $Folder "bin\cloudflared.exe"
if (Test-Path $Tunnel) { Ok "installed" } elseif ($Check) { Todo "would download cloudflared (about 40 MB)" } else {
    New-Item -ItemType Directory -Force (Split-Path $Tunnel) | Out-Null
    Info "Downloading cloudflared (about 40 MB)..."
    Invoke-WebRequest $TunnelUrl -OutFile $Tunnel -UseBasicParsing
    Ok "installed"
}

# 7: settings (the rest are defaults; your pairing key is made fresh on this computer the first time Jarvis runs)
Step 7 "Settings"
$Config = Join-Path $Folder "config.json"
if (Test-Path $Config) { Ok "config.json is already there; keeping it" }
elseif ($Check) { Todo "would create config.json pointing at $Page" }
else {
    [IO.File]::WriteAllText($Config, "{`n  `"phone_page_url`": `"$Page`"`n}`n")
    Ok "config.json created"
}

# 8: Claude login
Step 8 "Log in to Claude"
$creds = Join-Path $HOME ".claude\.credentials.json"
if (Test-Path $creds) { Ok "already logged in" } elseif ($Check) { Todo "would open Claude to log in" } else {
    Info "Claude opens here. Type /login, finish in the browser, then type /exit to come back."
    Read-Host "       Press Enter to open Claude" | Out-Null
    & claude
    if (Test-Path $creds) { Ok "logged in" } else { Info "Not logged in yet. Do it later: run 'claude', type /login." }
}

# 9: optional bits
Step 9 "Keys and extras"
if ($Check) { Todo "would offer: free AI keys, start at login, a Desktop shortcut" } else {
    if (Ask "Add your free Gemini / OpenRouter keys now? (the same keys as on your PC are fine)") { & $Py -m jarvis --keys }
    if (Ask "Start Jarvis by itself when you log in to Windows?") { & $Py -m jarvis --startup }
    if (Ask "Put a Jarvis shortcut on the Desktop and in the Start menu?") { & $Py -m jarvis.desktop --install | Out-Null; Ok "shortcut added" }
}

# 10: start him and show the QR code for the phone
Step 10 "Start Jarvis and connect your phone"
if ($Check) { Todo "would start Jarvis and open the 'Pair your phone' window"; Write-Host ""; exit 0 }
$log = Join-Path $Folder "jarvis.log"
$before = if (Test-Path $log) { (Get-Content $log).Count } else { 0 }
Start-Process wscript.exe -ArgumentList "`"$(Join-Path $Folder 'Jarvis.vbs')`""
Info "Starting (the first time he downloads his speech models, a few hundred MB)..."
$ready = $false
for ($i = 0; $i -lt 150 -and -not $ready; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Path $log) { $ready = [bool](Get-Content $log | Select-Object -Skip $before | Select-String "Told your phone|Phone server on") }
}
if ($ready) { Start-Sleep -Seconds 5; Ok "Jarvis is running (his icon is under the ^ arrow in the taskbar)" }
else { Info "He's taking a while; his icon appears under the ^ arrow in the taskbar when he's ready." }
Start-Process $Pyw -ArgumentList "-m jarvis.pairing" -WorkingDirectory $Folder

Write-Host ""
Write-Host "   Last step, on your iPhone:" -ForegroundColor Cyan
Write-Host "   - Point the Camera at the QR code that just opened."
Write-Host "   - Jarvis in Safari: tap the link. It adds this computer."
Write-Host "   - Jarvis on the Home Screen: long-press the link > Copy Link, then in Jarvis:"
Write-Host "     Settings > Your computers > Add a Computer."
Write-Host "   Switch computers at the top of Control Center (the computer tab)."
Write-Host ""
Write-Host "   Done." -ForegroundColor Green
Read-Host "Press Enter to close" | Out-Null
