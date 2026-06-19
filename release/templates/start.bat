@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo OrbBridge 시작 중...
start "OrbBridge" /D "%~dp0" "%~dp0OrbBridge.exe"
echo.
echo 브라우저가 자동으로 열리지 않으면 아래 주소를 여세요:
echo   http://127.0.0.1:8080/screen
echo   http://127.0.0.1:8080/table
echo.
echo 종료: OrbBridge 창을 닫거나 Ctrl+C
pause
