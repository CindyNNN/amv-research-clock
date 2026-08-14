@echo off & setlocal & cd /d "%~dp0" & set "PYTHONPATH=%~dp0src" & python scripts\push_tdx_cyb_indicator.py & pause
