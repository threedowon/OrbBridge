@echo off
chcp 65001 > nul
cd /d "%~dp0"

python sinesynth.py

if errorlevel 1 (
    echo.
    echo 실행 중 오류가 발생했다.
    pause
)