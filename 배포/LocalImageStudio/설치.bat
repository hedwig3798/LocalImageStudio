@echo off
rem Local Image Studio installer. ASCII-only on purpose (see the run script); messages are printed by setup\bootstrap.ps1.
setlocal
cd /d "%~dp0"
title Local Image Studio - Setup
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\bootstrap.ps1" %*
echo.
pause
