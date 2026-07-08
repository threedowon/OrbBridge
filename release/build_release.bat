@echo off
setlocal
set ROOT=%~dp0..
set SERVER=%ROOT%\server
set OUT=%~dp0dist\OrbBridge

echo [release] Installing build dependencies...
python -m pip install -q -r "%SERVER%\requirements-build.txt"
if errorlevel 1 exit /b 1

echo [release] Building OrbBridge...
cd /d "%SERVER%"
python -m PyInstaller launcher.spec --noconfirm --clean
if errorlevel 1 exit /b 1

echo [release] Building MelodyEditor...
python -m PyInstaller melody_editor.spec --noconfirm --clean
if errorlevel 1 exit /b 1

if exist "%OUT%" rmdir /s /q "%OUT%"
mkdir "%OUT%"

echo [release] Assembling release folder...
xcopy /E /I /Y /Q "%SERVER%\dist\OrbBridge\*" "%OUT%\"
copy /Y "%SERVER%\dist\MelodyEditor.exe" "%OUT%\" >nul

if not exist "%OUT%\melodies" mkdir "%OUT%\melodies"
if not exist "%OUT%\sounds" (
  if exist "%SERVER%\sounds" xcopy /E /I /Y /Q "%SERVER%\sounds" "%OUT%\sounds\" >nul
)

for %%F in (config.json orb_mapping.json stage_data.json voice_catalog.json) do (
  if not exist "%OUT%\%%F" if exist "%SERVER%\%%F" copy /Y "%SERVER%\%%F" "%OUT%\" >nul
)

echo [release] Creating OrbBridge.zip...
if exist "%~dp0OrbBridge.zip" del /f /q "%~dp0OrbBridge.zip"
powershell -NoProfile -Command "Compress-Archive -Path '%OUT%' -DestinationPath '%~dp0OrbBridge.zip' -Force"
if errorlevel 1 exit /b 1

echo.
echo Done:
echo   Folder: %OUT%
echo   Zip:    %~dp0OrbBridge.zip
echo.
echo Run: %OUT%\OrbBridge.exe
endlocal
