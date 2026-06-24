@echo off
setlocal
cd /d "%~dp0"

echo [MelodyEditor] Installing build dependencies...
python -m pip install -q pyinstaller
if errorlevel 1 exit /b 1

echo [MelodyEditor] Building executable...
python -m PyInstaller melody_editor.spec --noconfirm --clean
if errorlevel 1 exit /b 1

echo.
echo Done: dist\MelodyEditor.exe
echo Copy beside OrbBridge.exe — both use the same melodies\ folder.
endlocal
