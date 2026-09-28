@echo off
rem Re-analyse everything collected so far and open the chart.
setlocal
cd /d "%~dp0.."
set PYTHONUTF8=1
".venv\Scripts\python.exe" pipeline.py analyze --plot reports\tension.png
if exist "reports\tension.png" start "" "reports\tension.png"
pause
