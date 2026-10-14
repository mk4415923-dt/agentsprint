@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0MIGRATE_FROM_V3.ps1"
if errorlevel 1 pause
