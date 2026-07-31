@echo off
setlocal
cd /d "%~dp0\.."

if exist ".venv\Scripts\python.exe" goto install_venv
where py >nul 2>nul
if not errorlevel 1 goto install_py
where python >nul 2>nul
if not errorlevel 1 goto install_python

echo Python 3 was not found.
pause
exit /b 1

:install_venv
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -c "from sqvm.calibration import run_spectroscopy, run_rabi; print('SQVM import OK')"
goto finish

:install_py
py -3 -m pip install -e .
if errorlevel 1 goto failed
py -3 -c "from sqvm.calibration import run_spectroscopy, run_rabi; print('SQVM import OK')"
goto finish

:install_python
python -m pip install -e .
if errorlevel 1 goto failed
python -c "from sqvm.calibration import run_spectroscopy, run_rabi; print('SQVM import OK')"
goto finish

:failed
echo SQVM environment setup failed.
pause
exit /b 1

:finish
if errorlevel 1 goto failed
echo Environment setup completed.
pause
exit /b 0
