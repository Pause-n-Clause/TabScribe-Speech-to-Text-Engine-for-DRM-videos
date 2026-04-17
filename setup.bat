@echo off
echo.
echo  Tab Transcript - First-time Setup
echo  ==================================
echo.

:: Try PATH first
python --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON=python
    goto :install
)

:: Try py launcher (Python 3.9)
py -3.9 --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON=py -3.9
    goto :install
)

:: Try common install locations
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python39\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "C:\Python39\python.exe"
    "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python39\python.exe"
) do (
    if exist %%P (
        set PYTHON=%%P
        goto :install
    )
)

echo  [ERROR] Could not find Python automatically.
echo.
echo  Open setup.bat in Notepad and on the line that says SET PYTHON=
echo  paste the full path to your python.exe
echo.
pause
exit /b 1

:install
echo  Found Python at: %PYTHON%
echo  Installing dependencies...
%PYTHON% -m pip install -r requirements.txt
echo %PYTHON%> python_path.txt
echo.
echo  Setup complete! Run run.bat to start.
echo.
pause
