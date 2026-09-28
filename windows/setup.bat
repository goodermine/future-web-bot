@echo off
rem One-time setup: creates a private Python environment in the project,
rem installs the add-ons, and runs the demo so you can see it working.
setlocal
cd /d "%~dp0.."

where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
%PY% --version >nul 2>nul
if errorlevel 1 (
    echo Python was not found. Install Python 3.10 or newer from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during install. Then run this again.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating Python environment in .venv ...
    %PY% -m venv .venv || goto :fail
)

echo Installing add-ons (this can take a few minutes the first time) ...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail

echo.
echo Running the demo with made-up data ...
set PYTHONUTF8=1
".venv\Scripts\python.exe" pipeline.py demo || goto :fail

echo.
echo Setup complete. Opening the demo chart ...
start "" "reports\demo_tension.png"
echo Next: double-click windows\run_now.bat to do a real run,
echo then windows\schedule_hourly.bat to make it run every hour.
pause
exit /b 0

:fail
echo.
echo Something went wrong - see the messages above.
pause
exit /b 1
