@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo OrbBridge 진단 도구 시작...
echo main.py 가 실행 중이면 먼저 종료하세요 (같은 COM 포트를 공유할 수 없음)
python debug.py %*
pause
