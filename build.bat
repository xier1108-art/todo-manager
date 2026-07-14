@echo off
rem Rebuild the exe after editing app.py or web/. See build.ps1 for details.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
pause
