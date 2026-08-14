@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"

echo Deprecated: intraday task removed. Use run_cyb_signal_monitor_close.bat at 15:30.
call "%~dp0run_cyb_signal_monitor_close.bat"
exit /b %ERRORLEVEL%
