@echo off
chcp 65001 >nul
cd /d "%~dp0.."

set "PYTHON_CMD=py"
where py >nul 2>&1 || set "PYTHON_CMD=python"
%PYTHON_CMD% -m tools.midi_debug %*
if errorlevel 1 pause
