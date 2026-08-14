@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"

rem Manual only: prompt 0AMV close (no email). Scheduled task uses close bat instead.
python scripts\prompt_0amv_ret.py
set "EXIT_CODE=%ERRORLEVEL%"

if /I not "%CYB_SCHEDULED%"=="1" pause
exit /b %EXIT_CODE%
