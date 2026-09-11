@echo off
REM Starts the phone server and leaves the window open, because the address and
REM the token it prints are what you type into the phone the first time.
REM
REM This is the office-machine launcher: leave it running and the phone can
REM press the same buttons the window has. Close the window to stop it.
cd /d "%~dp0"

where py.exe >nul 2>&1
if %errorlevel%==0 (
    py serve.py %*
    pause
    exit /b
)

where python.exe >nul 2>&1
if %errorlevel%==0 (
    python serve.py %*
    pause
    exit /b
)

echo Could not find a Python launcher on PATH.
echo Install Python from python.org and tick "Add python.exe to PATH".
pause
