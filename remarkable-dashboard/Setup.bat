@echo off
REM Double-click this on a fresh Windows install. It runs setup.ps1, which asks
REM Windows for administrator rights itself, so "Run as administrator" is not
REM required here.
REM
REM Installs Python, Git, rmapi and Tailscale, clones the project, registers the
REM scheduled tasks, turns on SSH and Remote Desktop, and stops the machine
REM sleeping. Then it prints the short list of things only you can do.
cd /d "%~dp0"

where powershell.exe >nul 2>&1
if errorlevel 1 (
    echo Could not find powershell.exe. This script needs Windows.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
pause
