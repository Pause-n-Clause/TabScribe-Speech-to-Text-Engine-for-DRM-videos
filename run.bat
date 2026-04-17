@echo off
echo.
echo  Starting Tab Transcript...
echo  Open http://127.0.0.1:5000 in your browser
echo.

:: Read saved python path from setup
set /p PYTHON=<python_path.txt

if "%PYTHON%"=="" (
    echo  [ERROR] Run setup.bat first!
    pause
    exit /b 1
)

start "" http://127.0.0.1:5000
%PYTHON% app.py
pause
