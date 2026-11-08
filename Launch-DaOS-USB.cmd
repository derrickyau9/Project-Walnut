@echo off
setlocal
cd /d "%~dp0"
python "%~dp0tools\launch_bsd_desktop.py" %* --image-dir "%~dp0build\DaOS_BSD_RPi4_USB_ADB_ARM64" --work-dir /home/dy09/daos-vm-usb-adb
set "vm_exit=%errorlevel%"
if not "%vm_exit%"=="0" pause
exit /b %vm_exit%
