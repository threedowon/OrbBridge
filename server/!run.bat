@echo off
cd /d "%~dp0"

start "OrbBridge Server" cmd /k python main.py

timeout /t 2 /nobreak >nul

start "" http://127.0.0.1:8080/screen
start "" http://127.0.0.1:8080/table
start "" http://127.0.0.1:8080/admin