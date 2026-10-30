@echo off
rem Local Image Studio launcher. Keep this file ASCII-only: cmd mis-parses UTF-8 batch files
rem with non-ASCII text (lines get cut), so Korean messages live in setup\launch_check.ps1 and Python.
setlocal
cd /d "%~dp0"
title Local Image Studio
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup\launch_check.ps1"
if errorlevel 1 goto fail
set "VIRTUAL_ENV=%~dp0venv"
set "PATH=%VIRTUAL_ENV%\Scripts;%PATH%"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%VIRTUAL_ENV%\Scripts\python.exe" "%~dp0app\launcher.py" %*
if errorlevel 1 goto fail
exit /b 0
:fail
pause
exit /b 1
