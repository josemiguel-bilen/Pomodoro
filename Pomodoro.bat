@echo off
rem Startet den Pomodoro-Timer ohne Konsolenfenster.
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "pomodoro.py"
    exit /b
)

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw "pomodoro.py"
    exit /b
)

where python >nul 2>nul
if %errorlevel%==0 (
    start "" python "pomodoro.py"
    exit /b
)

echo.
echo Python wurde nicht gefunden.
echo Installiere Python von https://www.python.org/downloads/
echo (beim Setup "Add python.exe to PATH" anhaken)
echo.
echo Alternativ: pomodoro.html doppelklicken - laeuft ohne Installation im Browser.
echo.
pause
