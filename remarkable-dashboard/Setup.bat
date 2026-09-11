@echo off
REM ===================================================================
REM  Daily Sheet -- set up the machine that runs everything.
REM
REM  Double-click this on a fresh Windows install. It works two ways:
REM  next to a checkout it runs the local setup.ps1, and on its own --
REM  downloaded by itself onto a bare machine -- it fetches the script
REM  first. So "download one file, double-click it" is all it takes.
REM
REM  setup.ps1 asks Windows for administrator rights itself, so there
REM  is no need to right-click -> Run as administrator.
REM ===================================================================
setlocal
cd /d "%~dp0"

set "BRANCH=ReMarkable-dashboard"
set "RAW=https://raw.githubusercontent.com/eidriaen/Claude-test/%BRANCH%/remarkable-dashboard/setup.ps1"

where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo Could not find powershell.exe. This script needs Windows.
    pause
    exit /b 1
)

if exist "%~dp0setup.ps1" (
    echo Running setup.ps1 from this folder...
    set "SCRIPT=%~dp0setup.ps1"
) else (
    echo Fetching setup.ps1 from GitHub...
    REM Downloaded into C:\Users\Public: %TEMP% sits under the user profile,
    REM and a profile name with a non-ASCII character in it is exactly the
    REM kind of thing that breaks a path handed from cmd to PowerShell.
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest '%RAW%' -OutFile '%PUBLIC%\setup.ps1' -UseBasicParsing"
    if errorlevel 1 (
        echo.
        echo Could not download the setup script. Check this machine is online.
        pause
        exit /b 1
    )
    set "SCRIPT=%PUBLIC%\setup.ps1"
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
echo.
pause
