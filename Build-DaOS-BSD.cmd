@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  python "system\bsd-buildroot\daos.py" all
) else (
  python "system\bsd-buildroot\daos.py" %*
)
exit /b %errorlevel%
