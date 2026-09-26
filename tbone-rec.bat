@echo off
REM Launcher for tbone-rec. Finds (or installs, via install.ps1) tbone-rec,
REM then runs it. Output is logged since tbone-rec.vbs runs this with no
REM visible console window.

set "PATH=%USERPROFILE%\.local\bin;%PATH%"
set "LOGDIR=%LOCALAPPDATA%\tbone-recorder\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>nul
set "LOGFILE=%LOGDIR%\tbone-rec.log"

echo ---%date% %time%: launching tbone-rec--- >> "%LOGFILE%"

where tbone-rec >nul 2>nul
if errorlevel 1 (
    echo tbone-rec not found; installing... >> "%LOGFILE%"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/Kdman0code/tbone-recorder/main/install.ps1 | iex" >> "%LOGFILE%" 2>&1
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

where tbone-rec >nul 2>nul
if errorlevel 1 (
    echo could not find or install tbone-rec >> "%LOGFILE%"
    powershell -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('Could not install tbone-rec. See %LOGFILE% for details.','t.bone recorder') | Out-Null"
    exit /b 1
)

tbone-rec >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    powershell -NoProfile -WindowStyle Hidden -Command "Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('tbone-rec exited with an error. See %LOGFILE% for details.','t.bone recorder') | Out-Null"
)
