@echo off
rem One collection cycle. This is what the hourly schedule runs.
rem Output is appended to logs\webbot.log.
setlocal
cd /d "%~dp0.."
if not exist logs mkdir logs
set PYTHONUTF8=1
echo ===== %date% %time% ===== >> "logs\webbot.log"
".venv\Scripts\python.exe" pipeline.py run --sources sources.txt >> "logs\webbot.log" 2>&1
exit /b %errorlevel%
