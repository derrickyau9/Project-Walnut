@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0system\adb\connect_usb.py" %*
) else (
  python "%~dp0system\adb\connect_usb.py" %*
)
exit /b %errorlevel%
