#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$ReleaseDir = $PSScriptRoot
$Root = Split-Path -Parent $ReleaseDir
$Server = Join-Path $Root "server"
$Deliver = Join-Path $ReleaseDir "OrbBridge"
$ZipPath = Join-Path $ReleaseDir "OrbBridge.zip"

Write-Host "== OrbBridge release build ==" -ForegroundColor Cyan

Set-Location $ReleaseDir

Write-Host "Installing build deps..."
python -m pip install -q pyinstaller python-osc websockets pyserial

Write-Host "Running PyInstaller..."
python -m PyInstaller orbbridge.spec --noconfirm --distpath dist --workpath build

$Built = Join-Path $ReleaseDir "dist\OrbBridge"
if (-not (Test-Path (Join-Path $Built "OrbBridge.exe"))) {
    throw "Build failed: OrbBridge.exe not found in dist\OrbBridge"
}

Write-Host "Assembling delivery folder..."
if (Test-Path $Deliver) { Remove-Item $Deliver -Recurse -Force }
New-Item -ItemType Directory -Path $Deliver | Out-Null
Copy-Item -Path (Join-Path $Built "*") -Destination $Deliver -Recurse -Force
Copy-Item (Join-Path $ReleaseDir "templates\README.txt") $Deliver

Write-Host "Creating zip..."
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
Start-Sleep -Milliseconds 500
Compress-Archive -Path (Join-Path $Deliver "*") -DestinationPath $ZipPath -Force

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "  Folder: $Deliver"
Write-Host "  Zip:    $ZipPath"
Write-Host ""
Write-Host "Share OrbBridge.zip (Windows only) — recipient unzips and runs OrbBridge.exe"
