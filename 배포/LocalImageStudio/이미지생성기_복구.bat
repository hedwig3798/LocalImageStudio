@echo off
rem Stops only the processes this app started and clears stale state. ASCII-only on purpose.
setlocal
cd /d "%~dp0"
title Local Image Studio - Stop / Repair
if not exist "%~dp0venv\Scripts\python.exe" goto done
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\launch_check.ps1" >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%~dp0venv\Scripts\python.exe" "%~dp0app\launcher.py" --repair
:done
echo.
pause
