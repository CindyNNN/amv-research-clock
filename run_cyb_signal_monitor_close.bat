@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"

rem Weekday 15:30 only: prompt 0AMV close, then send close email.
python scripts\prompt_0amv_ret.py
if errorlevel 1 goto :done

python scripts\run_cyb_signal_monitor.py --mode close
if errorlevel 1 goto :done

:done
set "EXIT_CODE=%ERRORLEVEL%"
if /I not "%CYB_SCHEDULED%"=="1" pause
exit /b %EXIT_CODE%
