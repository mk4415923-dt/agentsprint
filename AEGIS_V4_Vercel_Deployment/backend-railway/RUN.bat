@echo off
cd /d "%~dp0"
title AEGIS Enterprise Helpdesk
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0RUN_ENTERPRISE.ps1"
if errorlevel 1 pause
