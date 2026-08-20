@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON_CMD=py"
where py >nul 2>&1 || set "PYTHON_CMD=python"

start "OrbBridge Server" cmd /k "%PYTHON_CMD% -m server.launcher"
timeout /t 2 /nobreak >nul

start "" http://127.0.0.1:8080/admin
start "" http://127.0.0.1:8080/admin/software
