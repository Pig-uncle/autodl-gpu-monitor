@echo off
cd /d "%~dp0"
echo Keep the original service window open while monitoring.
echo Closing the original service window stops the local service.
"%~dp0AutoDL-Monitor.exe"
if errorlevel 1 (
  echo Unable to start the service. Please read the message above.
  pause
)
