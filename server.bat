@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo OrbBridge (Python dev) — bridge + main
start "OrbBridge Bridge" /D "%~dp0" python bridge\bridge.py
timeout /t 2 /nobreak >nul
cd server
python main.py
pause
