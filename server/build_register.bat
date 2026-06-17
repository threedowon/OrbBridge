@echo off
setlocal
cd /d "%~dp0"

echo [OrbRegister] Installing build dependencies...
python -m pip install -r requirements-register.txt
if errorlevel 1 exit /b 1

echo [OrbRegister] Building executable...
python -m PyInstaller register.spec --noconfirm
if errorlevel 1 exit /b 1

set OUT=dist\OrbRegister
if exist config.json copy /Y config.json "%OUT%\" >nul
if exist orb_mapping.json copy /Y orb_mapping.json "%OUT%\" >nul

echo.
echo Done: %OUT%\OrbRegister.exe
echo Config / mapping JSON are in the same folder as the exe.
endlocal
