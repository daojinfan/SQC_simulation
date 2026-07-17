@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto run_venv

where py >nul 2>nul
if not errorlevel 1 goto run_py

where python >nul 2>nul
if not errorlevel 1 goto run_python

echo Python 3 was not found. Install Python 3 or create the project .venv first.
pause
exit /b 1

:run_venv
".venv\Scripts\python.exe" "scripts\start_calibration_web.py" %*
goto finish

:run_py
py -3 "scripts\start_calibration_web.py" %*
goto finish

:run_python
python "scripts\start_calibration_web.py" %*

:finish
set "exit_code=%errorlevel%"
if not "%exit_code%"=="0" pause
exit /b %exit_code%
