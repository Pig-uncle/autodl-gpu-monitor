@echo off
cd /d "%~dp0"
python -m PyInstaller --noconfirm --onedir --name AutoDL-Monitor --add-data "web;web" share_main.py
if errorlevel 1 exit /b 1
copy /Y "start.bat" "dist\AutoDL-Monitor\start.bat" >nul
echo Built dist\AutoDL-Monitor. Keep the entire folder together.
