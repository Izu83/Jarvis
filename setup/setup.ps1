# Jarvis setup: puts Jarvis on this computer (your laptop, or a new PC) and starts it.
# jarvis-setup.exe shows a window and runs this hidden with -Gui; it can also run by hand in a console.
# Safe to run again: it skips what's already done and updates Jarvis.
#   -Check      only look: say what's installed and what would be done, change nothing
#   -Gui        talk to the window: "@@kind|text" lines instead of colours and questions
#   -Keys -Startup -Shortcut   the window's choices (in a console it asks)
# Keep this file plain ASCII: Windows PowerShell 5.1 reads a file without a BOM in the old code page.
param([switch]$Check, [switch]$Gui, [string]$Folder = "", [switch]$Keys, [switch]$Startup, [switch]$Shortcut)

# Native programs (git, pip) write progress to stderr; with "Stop" Windows PowerShell would treat that as a failure.
# Every step checks $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"   # the progress bar makes downloads in Windows PowerShell 10x slower
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
try { [Console]::OutputEncoding = New-Object Text.UTF8Encoding $false } catch {}   # UTF-8 for the window, no BOM
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:PYTHONUTF8 = "1"
$Repo = "https://github.com/Izu83/Jarvis.git"
$Page = "https://izu83.github.io/Jarvis/"
$TunnelUrl = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
$Steps = 10
if (-not $Gui) { try { $Host.UI.RawUI.WindowTitle = "Jarvis setup" } catch {} }

function Say($kind, $text) { if ($Gui) { [Console]::Out.WriteLine("@@$kind|$text"); [Console]::Out.Flush() } }
function Step($n, $text) { Say "step" "$n|$text"; if (-not $Gui) { Write-Host ""; Write-Host "[$n/$Steps] $text" -ForegroundColor Cyan } }
function Ok($text) { Say "ok" $text; if (-not $Gui) { Write-Host "   OK  $text" -ForegroundColor Green } }
function Todo($text) { Say "info" $text; if (-not $Gui) { Write-Host "   ->  $text" -ForegroundColor Yellow } }
function Info($text) { Say "info" $text; if (-not $Gui) { Write-Host "       $text" } }
function Wait($text) { Say "wait" $text; if (-not $Gui) { Write-Host "       $text" -ForegroundColor Yellow } }
function Fail($text) {
    Say "fail" $text
    if (-not $Gui) {
        Write-Host ""; Write-Host "   !!  $text" -ForegroundColor Red
        Write-Host "       Fix that and run jarvis-setup.exe again: it carries on where it stopped."
        if (-not $Check) { Read-Host "Press Enter to close" | Out-Null }
    }
    exit 1
}
function Ask($q, $choice) {
    if ($Gui) { return [bool]$choice }
    $a = Read-Host "       $q [Y/n]"; return ($a.Trim() -eq "" -or $a.Trim() -match '^[yY]')
}
function Run {   # a program, its output (stdout and stderr) passed on as plain lines for the window's details
    $exe, $rest = $args   # plain $args: an advanced function would take "--progress" or "-m" as its own parameters
    & $exe @rest 2>&1 | ForEach-Object { "$_" }
}
function Console-Window($exe, $arguments, $dir) {   # something you type into: its own window, and wait for it
    $p = @{ FilePath = $exe; Wait = $true; WorkingDirectory = $dir }
    if ($arguments) { $p.ArgumentList = $arguments }
    Start-Process @p
}
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
    Wait "Installing $name. If Windows asks, allow it."
    Run winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements
    RefreshPath
}

if (-not $Gui) {
    Write-Host ""
    Write-Host "   J A R V I S   setup" -ForegroundColor Cyan
    Write-Host "   Installs what Jarvis needs, downloads him, and connects your phone."
    if ($Check) { Write-Host "   (check only: nothing is changed)" -ForegroundColor Yellow }
}
RefreshPath

# 1-3: the programs Jarvis runs on
Step 1 "Git"
if (Has "git") { Ok "Already installed" } elseif ($Check) { Todo "Would install Git" } else {
    Winget "Git.Git" "Git"
    if (-not (Has "git")) { Fail "Git didn't install." }
    Ok "Installed"
}

Step 2 "Python 3.13"
if (HasPython) { Ok "Already installed" } elseif ($Check) { Todo "Would install Python 3.13" } else {
    Winget "Python.Python.3.13" "Python 3.13"
    if (-not (HasPython)) { Fail "Python 3.13 didn't install." }
    Ok "Installed"
}

Step 3 "Claude Code"
if (Has "claude") { Ok "Already installed" } elseif ($Check) { Todo "Would install Claude Code" } else {
    Info "Installing Claude Code..."
    try { Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression 2>&1 | ForEach-Object { "$_" } } catch { "$_" }
    RefreshPath
    if (-not (Has "claude")) { Fail "Claude Code didn't install." }
    Ok "Installed"
}

# 4: where Jarvis goes
Step 4 "Download Jarvis"
if (-not $Folder) {
    $Folder = Join-Path $HOME "Jarvis"
    if (-not $Check -and -not $Gui -and -not (Test-Path (Join-Path $Folder ".git"))) {
        Info "Jarvis goes in $Folder"
        if (-not (Ask "Is that folder OK?" $true)) { $Folder = (Read-Host "       Folder for Jarvis").Trim('" ') }
    }
}
if (Test-Path (Join-Path $Folder ".git")) {
    if ($Check) { Ok "Already in $Folder (would update it)" } else {
        Info "Getting the latest version..."
        Push-Location $Folder; Run git pull --ff-only; $pulled = $LASTEXITCODE; Pop-Location
        if ($pulled -eq 0) { Ok "Up to date in $Folder" } else { Ok "Kept as it is (it has local changes)" }
    }
} elseif ((Test-Path $Folder) -and (Get-ChildItem $Folder -Force | Select-Object -First 1)) {
    Fail "$Folder already has other files in it. Pick an empty folder."
} elseif ($Check) { Todo "Would download Jarvis into $Folder" } else {
    Info "Downloading into $Folder..."
    Run git clone --progress $Repo $Folder
    if ($LASTEXITCODE -ne 0) { Fail "Couldn't download Jarvis from GitHub. Check the internet connection." }
    Ok "Downloaded to $Folder"
}
if ($Check -and -not (Test-Path $Folder)) {
    Info "The rest happens inside that folder once it's there."
    exit 0
}
Set-Location $Folder
$Py = Join-Path $Folder ".venv\Scripts\python.exe"
$Pyw = Join-Path $Folder ".venv\Scripts\pythonw.exe"

# 5: Python packages. The NVIDIA ones (about 1.5 GB) only where there's an NVIDIA card to use them. No pip cache:
# one left by another pip version only gives a "Cache entry deserialization failed" warning per package.
Step 5 "Python packages"
$nvidia = [bool](Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" })
if ($Check) { Todo $(if (Test-Path $Py) { "Would update the packages" } else { "Would install the packages" }) } else {
    if (-not (Test-Path $Py)) {
        Info "Making Jarvis's Python..."
        Run py -3.13 -m venv .venv
        if (-not (Test-Path $Py)) { Fail "Couldn't make the Python environment." }
    }
    $req = "requirements.txt"
    if (-not $nvidia) {
        $req = Join-Path $env:TEMP "jarvis-requirements.txt"
        Get-Content requirements.txt | Where-Object { $_ -notmatch '^nvidia-' } | Set-Content $req -Encoding ascii
    }
    Info $(if ($nvidia) { "NVIDIA card found: speech will run on it" } else { "No NVIDIA card: skipping its 1.5 GB of packages" })
    Run $Py -m pip install --no-cache-dir --progress-bar off -r $req
    if ($LASTEXITCODE -ne 0) { Fail "Installing the packages failed. Details shows why." }
    Ok "Installed"
}

# 6: the tunnel, so your phone can reach this computer from anywhere
Step 6 "Tunnel for your phone"
$Tunnel = Join-Path $Folder "bin\cloudflared.exe"
if (Test-Path $Tunnel) { Ok "Already installed" } elseif ($Check) { Todo "Would download cloudflared (about 40 MB)" } else {
    New-Item -ItemType Directory -Force (Split-Path $Tunnel) | Out-Null
    Info "Downloading cloudflared (about 40 MB)..."
    try { Invoke-WebRequest $TunnelUrl -OutFile $Tunnel -UseBasicParsing } catch { Fail "Couldn't download cloudflared: $_" }
    Ok "Installed"
}

# 7: settings (the rest are defaults; your pairing key is made fresh on this computer the first time Jarvis runs)
Step 7 "Settings"
$Config = Join-Path $Folder "config.json"
if (Test-Path $Config) { Ok "Keeping your settings" }
elseif ($Check) { Todo "Would create config.json" }
else {
    [IO.File]::WriteAllText($Config, "{`n  `"phone_page_url`": `"$Page`"`n}`n")
    Ok "Created"
}

# 8: Claude login
Step 8 "Log in to Claude"
$creds = Join-Path $HOME ".claude\.credentials.json"
if (Test-Path $creds) { Ok "Already logged in" } elseif ($Check) { Todo "Would open Claude to log in" } else {
    if ($Gui) {
        Wait "In the Claude window: type /login, finish in the browser, then type /exit"
        Console-Window "claude" $null $Folder
    } else {
        Info "Claude opens here. Type /login, finish in the browser, then type /exit to come back."
        Read-Host "       Press Enter to open Claude" | Out-Null
        & claude
    }
    if (Test-Path $creds) { Ok "Logged in" } else { Ok "Skipped: log in later with 'claude' then /login" }
}

# 9: optional bits
Step 9 "Extras"
if ($Check) { Todo "Would offer: free AI keys, start at login, a Desktop shortcut" } else {
    $done = @()
    if (Ask "Add your free Gemini / OpenRouter keys now? (the same keys as on your PC are fine)" $Keys) {
        Wait "Paste your keys in the window that opened"
        if ($Gui) { Console-Window $Py "-m jarvis --keys" $Folder } else { & $Py -m jarvis --keys }
        $done += "keys"
    }
    if (Ask "Start Jarvis by itself when you log in to Windows?" $Startup) { Run $Py -m jarvis --startup; $done += "starts at login" }
    if (Ask "Put a Jarvis shortcut on the Desktop and in the Start menu?" $Shortcut) { Run $Py -m jarvis.desktop --install; $done += "shortcut" }
    $t = $done -join ", "
    Ok $(if ($t) { $t.Substring(0, 1).ToUpper() + $t.Substring(1) } else { "None picked" })
}

# 10: start him and show the QR code for the phone
Step 10 "Start Jarvis"
if ($Check) { Todo "Would start Jarvis and open the 'Pair your phone' window"; exit 0 }
$log = Join-Path $Folder "jarvis.log"
$before = if (Test-Path $log) { (Get-Content $log).Count } else { 0 }
Start-Process wscript.exe -ArgumentList "`"$(Join-Path $Folder 'Jarvis.vbs')`""
Info "Starting. The first time he downloads his speech models (a few hundred MB)..."
$ready = $false
for ($i = 0; $i -lt 150 -and -not $ready; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Path $log) {
        $new = @(Get-Content $log | Select-Object -Skip $before)
        $ready = [bool]($new | Select-String "Told your phone|Phone server on")
        if (-not $ready -and $new.Count) { $last = $new[-1] -replace '^\d\d:\d\d:\d\d \S+: ', ''; if ($last.Length -gt 90) { $last = $last.Substring(0, 90) + "..." }; Info $last }
    }
}
if ($ready) { Start-Sleep -Seconds 5; Ok "Running. His icon is under the ^ arrow in the taskbar" }
else { Ok "Still starting. His icon appears under the ^ arrow in the taskbar when he's ready" }
Start-Process $Pyw -ArgumentList "-m jarvis.pairing" -WorkingDirectory $Folder
Say "done" $Folder

if (-not $Gui) {
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
}
