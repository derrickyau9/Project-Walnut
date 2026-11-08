@echo off
setlocal
cd /d "%~dp0"
python "%~dp0tools\launch_bsd_desktop.py" %*
set "vm_exit=%errorlevel%"
if not "%vm_exit%"=="0" pause
exit /b %vm_exit%
