@echo off
rem Startet den Pomodoro-Timer und meldet sich, wenn etwas fehlt.
cd /d "%~dp0"

if not exist "pomodoro.py" (
    echo.
    echo pomodoro.py liegt nicht in diesem Ordner:
    echo   %~dp0
    echo.
    echo Diese Datei muss neben Pomodoro.bat liegen. Beim Entpacken eines ZIPs
    echo bitte den kompletten Ordner entpacken, nicht nur die Batchdatei.
    echo.
    pause
    exit /b 1
)

rem Konsolen-Python zum Pruefen finden
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY (where python >nul 2>nul && set "PY=python")

if not defined PY (
    echo.
    echo Python wurde nicht gefunden.
    echo.
    echo Installiere Python von https://www.python.org/downloads/
    echo Wichtig: beim Setup "Add python.exe to PATH" anhaken.
    echo.
    echo Ohne Installation laeuft pomodoro.html im Browser - einfach doppelklicken.
    echo.
    pause
    exit /b 1
)

rem Ist Tkinter dabei? (fehlt, wenn "tcl/tk and IDLE" abgewaehlt wurde)
%PY% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
    echo.
    echo Diese Python-Installation hat kein Tkinter.
    echo.
    echo Reparieren: Einstellungen ^> Apps ^> Python ^> Aendern ^> Modify
    echo und dabei "tcl/tk and IDLE" anhaken.
    echo.
    echo Ohne Installation laeuft pomodoro.html im Browser - einfach doppelklicken.
    echo.
    pause
    exit /b 1
)

rem Fensterloses Python bevorzugen, damit keine schwarze Konsole stehen bleibt
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

start "" %PY% "pomodoro.py"
exit /b
