@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."

set "PYTHON_CMD=py"
where py >nul 2>&1 || set "PYTHON_CMD=python"
echo OrbBridge 진단 도구 시작
echo server.launcher가 실행 중이면 같은 COM 포트를 열 수 없습니다.
%PYTHON_CMD% -m tools.debug %*
pause
