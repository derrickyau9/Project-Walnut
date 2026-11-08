@echo off
setlocal
cd /d "%~dp0"
python "%~dp0tools\launch_bsd_desktop.py" start --image-dir "%~dp0build\DaOS_R14_RPi4_ARM64" --work-dir /home/dy09/daos-vm-rpi4-preview
set "vm_exit=%errorlevel%"
if not "%vm_exit%"=="0" pause
exit /b %vm_exit%
