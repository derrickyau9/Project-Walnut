@echo off
setlocal
cd /d "%~dp0"
set "DAOS_UI_PYTHON=%ProgramData%\anaconda3\python.exe"
if not exist "%DAOS_UI_PYTHON%" set "DAOS_UI_PYTHON=python"
"%DAOS_UI_PYTHON%" -B "%~dp0ui\on-device\main.py" --native-command "%~dp0build\native-r11\Release\hf-camerad.exe" --data-dir "%~dp0var\DaOS-experience" %*
exit /b %errorlevel%
