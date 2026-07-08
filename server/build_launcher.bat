@echo off
setlocal
cd /d "%~dp0"

echo [OrbBridge] Installing build dependencies...
python -m pip install -q -r requirements-build.txt
if errorlevel 1 exit /b 1

echo [OrbBridge] Building executable...
python -m PyInstaller launcher.spec --noconfirm --clean
if errorlevel 1 exit /b 1

echo.
echo Done: dist\OrbBridge\OrbBridge.exe
endlocal
