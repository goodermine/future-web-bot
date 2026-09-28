@echo off
rem Removes the hourly FutureWebBot task. Your collected data is kept.
schtasks /Delete /F /TN "FutureWebBot"
pause
