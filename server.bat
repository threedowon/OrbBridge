@echo off
cd /d "%~dp0"
echo Voice Orb (dev) - bridge + main
start "Voice Orb Bridge" /D "%~dp0" cmd /c "python bridge\bridge.py & pause"
timeout /t 2 /nobreak >nul
cd server
python main.py
pause
