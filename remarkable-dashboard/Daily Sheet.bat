@echo off
REM Launcher for the dashboard window. Right-click -> Send to -> Desktop
REM (create shortcut) to get a clickable icon.
REM
REM pyw.exe / pythonw.exe run without a console window behind the app; if
REM neither is on PATH we fall back to py so an error is at least visible.
cd /d "%~dp0"

where pyw.exe >nul 2>&1 && (
    start "" pyw.exe "dashboard.pyw"
    exit /b
)
where pythonw.exe >nul 2>&1 && (
    start "" pythonw.exe "dashboard.pyw"
    exit /b
)
where py.exe >nul 2>&1 && (
    py "dashboard.pyw"
    exit /b
)

echo Could not find a Python launcher on PATH.
echo Install Python from python.org and tick "Add python.exe to PATH".
pause
