@echo off
REM ===================================================================
REM  Open a shell on the mini PC.
REM
REM  Double-click. It asks for the address the first time and remembers
REM  it after that. Windows already has an SSH client; if the feature is
REM  switched off, connect.ps1 turns it on.
REM
REM  Right-click -> Send to -> Desktop (create shortcut) for an icon.
REM ===================================================================
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0connect.ps1" %*
echo.
pause
