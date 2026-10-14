@echo off
setlocal
cd /d "%~dp0"
echo This transfers the old AEGIS Pro data, policy, generated output and SQLite case history.
echo Close both AEGIS programs before migrating.
set /p OLD="Enter FULL path to extracted OLD AEGIS_Pro_Helpdesk folder: "
if not exist "%OLD%\data\inbox" (
  echo [ERROR] No data\inbox found at that path.
  pause
  exit /b 1
)
python migrate_v2.py "%OLD%"
if errorlevel 1 (
  echo [ERROR] Migration incomplete - see message above.
) else (
  echo Migration successful. Now run RUN.bat.
)
pause
