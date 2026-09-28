@echo off
rem Registers a Windows Task Scheduler job named "FutureWebBot" that runs
rem run_webbot.bat every hour while you are logged in. Safe to run again.
setlocal
schtasks /Create /F /SC HOURLY /TN "FutureWebBot" /TR "\"%~dp0run_webbot.bat\""
if errorlevel 1 (
    echo Could not create the scheduled task.
) else (
    echo Scheduled: FutureWebBot will run every hour.
    echo To check it, open Task Scheduler and look for "FutureWebBot".
    echo To stop it, double-click windows\remove_schedule.bat
)
pause
