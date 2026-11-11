# Starts the API (port 8000) and the website (port 3000) in two windows,
# waits until the API is ready, then opens the browser.
#   -Dev   auto-restart the API when code changes (for development)
param([switch]$Dev)
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Test-Path "$root\backend\.venv\Scripts\python.exe")) {
  Write-Host "Setup has not been run yet. Run this first:" -ForegroundColor Red
  Write-Host "  powershell -ExecutionPolicy Bypass -File setup.ps1" -ForegroundColor Yellow
  exit 1
}
if (-not (Test-Path "$root\frontend\node_modules")) {
  Write-Host "Website packages are missing. Run setup.ps1 (or 'npm install' inside the frontend folder)." -ForegroundColor Red
  exit 1
}

$reload = if ($Dev) { "--reload --reload-dir stockora" } else { "" }
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; .\.venv\Scripts\python.exe -m uvicorn stockora.api.main:app $reload --port 8000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\frontend'; npm run dev"

Write-Host "Starting Stockora. The backend trains the forecasting model on every start." -ForegroundColor Cyan
Write-Host "On a laptop this can take 2-5 minutes (longer the very first time). Please wait..." -ForegroundColor Cyan
$ready = $false
$waitMinutes = 10
for ($i = 0; $i -lt ($waitMinutes * 30); $i++) {
  if ($i -gt 0 -and $i % 30 -eq 0) { Write-Host "  still starting... $([int]($i / 30)) min" -ForegroundColor DarkGray }
  try {
    $r = Invoke-WebRequest -Uri "http://localhost:8000/health" -UseBasicParsing -TimeoutSec 2
    if ($r.StatusCode -eq 200) { $ready = $true; break }
  } catch { }
  Start-Sleep -Seconds 2
}
if ($ready) {
  Write-Host "Backend ready. Opening http://localhost:3000" -ForegroundColor Green
  Start-Process "http://localhost:3000"
} else {
  Write-Host "The backend did not start within $waitMinutes minutes." -ForegroundColor Red
  Write-Host "Look at the backend window: if it shows an error, send a screenshot. If it shows" -ForegroundColor Yellow
  Write-Host "'Application startup complete', open http://localhost:3000 yourself." -ForegroundColor Yellow
}
