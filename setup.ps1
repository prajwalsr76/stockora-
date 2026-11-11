# One-time setup on Windows. Run from the stockora folder:
#   powershell -ExecutionPolicy Bypass -File setup.ps1
# Safe to run again: it repairs a half-finished setup. A full log is written to setup.log.

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
Start-Transcript -Path "$root\setup.log" -Force | Out-Null

function Fail($msg, $hint) {
  Write-Host ""
  Write-Host "SETUP STOPPED: $msg" -ForegroundColor Red
  if ($hint) { Write-Host $hint -ForegroundColor Yellow }
  Write-Host "Full log: $root\setup.log  (send a screenshot of this window if you need help)" -ForegroundColor Gray
  Stop-Transcript | Out-Null
  exit 1
}

# ---------------------------------------------------------------- 1. find a usable Python (3.11 - 3.13)
Write-Host "[1/5] Looking for Python 3.11, 3.12 or 3.13..." -ForegroundColor Cyan
$py = $null
$candidates = @(@("py", "-3.13"), @("py", "-3.12"), @("py", "-3.11"), @("python"), @("python3"))
foreach ($c in $candidates) {
  $exe = $c[0]; $args_ = @($c | Select-Object -Skip 1)
  if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
  $path = (Get-Command $exe).Source
  if ($path -like "*WindowsApps*") { continue }            # Microsoft Store shortcut, not a real install
  $ver = & $exe @args_ -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
  if ($LASTEXITCODE -ne 0 -or -not $ver) { continue }
  $minor = [int]($ver.Split(".")[1])
  if ($ver.StartsWith("3.") -and $minor -ge 11 -and $minor -le 13) { $py = @($exe) + $args_; break }
  Write-Host "  found Python $ver at $path (not supported, skipping)" -ForegroundColor DarkYellow
}
if (-not $py) {
  Fail "No supported Python found." ("Install Python 3.12 from https://www.python.org/downloads/ " +
    "and tick 'Add python.exe to PATH' on the first screen. Then open a NEW PowerShell window and run setup.ps1 again.")
}
$pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
Write-Host ("  using: " + ($py -join " ") + "  (Python " + (& $pyExe @pyArgs -c "import sys; print(sys.version.split()[0])") + ")") -ForegroundColor Green

if ($root -like "*OneDrive*") {
  Write-Host "  note: this folder is inside OneDrive. If installs fail with 'Access is denied' or 'file in use'," -ForegroundColor DarkYellow
  Write-Host "        pause OneDrive syncing (OneDrive icon > Pause syncing) while setup runs." -ForegroundColor DarkYellow
}

# ---------------------------------------------------------------- 2. virtual environment (rebuild if broken)
Write-Host "[2/5] Creating the Python environment (backend\.venv)..." -ForegroundColor Cyan
Set-Location "$root\backend"
$venvPy = "$root\backend\.venv\Scripts\python.exe"
if (Test-Path $venvPy) {
  & $venvPy -c "import sys; assert sys.version_info[:2] >= (3, 11)" 2>$null
  if ($LASTEXITCODE -ne 0) { Write-Host "  existing .venv is broken, rebuilding" -ForegroundColor DarkYellow; Remove-Item -Recurse -Force .venv }
}
if (-not (Test-Path $venvPy)) {
  & $pyExe @pyArgs -m venv .venv
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) { Fail "Could not create the Python environment." "Try running PowerShell as your normal user (not inside OneDrive-locked folders)." }
}

# ---------------------------------------------------------------- 3. Python packages
Write-Host "[3/5] Installing Python packages (2-5 minutes the first time)..." -ForegroundColor Cyan
& $venvPy -m pip install --upgrade pip --disable-pip-version-check
& $venvPy -m pip install -r requirements.txt --disable-pip-version-check --prefer-binary
if ($LASTEXITCODE -ne 0) {
  Fail "Installing Python packages failed (see the red lines above)." ("Common fixes: check your internet connection; " +
    "pause OneDrive syncing; if it says 'Microsoft Visual C++ 14.0 is required', you are on an unsupported Python - install Python 3.12.")
}
& $venvPy -c "import fastapi, uvicorn, sklearn, pandas, langgraph" 2>$null
if ($LASTEXITCODE -ne 0) { Fail "Packages installed but cannot be imported." "Delete the backend\.venv folder and run setup.ps1 again." }
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "  created backend\.env - add your GROQ_API_KEY there (optional)." -ForegroundColor Yellow }

# ---------------------------------------------------------------- 4. quick self-test (does not stop setup)
Write-Host "[4/5] Running the test suite..." -ForegroundColor Cyan
& $venvPy -m pytest -q -x
if ($LASTEXITCODE -ne 0) { Write-Host "  some tests failed - the app may still run; include this window in your screenshot." -ForegroundColor DarkYellow }

# ---------------------------------------------------------------- 5. website packages
Write-Host "[5/5] Installing website packages..." -ForegroundColor Cyan
Set-Location "$root\frontend"
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
  Fail "Node.js is not installed." "Install the LTS version from https://nodejs.org, open a NEW PowerShell window and run setup.ps1 again."
}
npm install --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { Fail "npm install failed." "Check your internet connection, pause OneDrive syncing, and run setup.ps1 again." }

Set-Location $root
Write-Host ""
Write-Host "Setup complete. Start Stockora with:" -ForegroundColor Green
Write-Host "  powershell -ExecutionPolicy Bypass -File start.ps1" -ForegroundColor Green
Stop-Transcript | Out-Null
