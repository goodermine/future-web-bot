@echo off
rem Run one collection cycle right now, then show the log tail and chart.
setlocal
cd /d "%~dp0.."
call "%~dp0run_webbot.bat"
if errorlevel 1 (
    echo The run failed. Last lines of logs\webbot.log:
) else (
    echo Run finished. Last lines of logs\webbot.log:
)
powershell -NoProfile -Command "Get-Content 'logs\webbot.log' -Tail 25"
if exist "reports\tension.png" start "" "reports\tension.png"
pause
