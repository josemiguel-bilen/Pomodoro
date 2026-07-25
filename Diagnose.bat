@echo off
rem Startet den Timer mit sichtbarer Konsole. Das Fenster bleibt offen,
rem damit eine Fehlermeldung lesbar ist.
cd /d "%~dp0"

echo ============================================================
echo  Pomodoro - Diagnose
echo ============================================================
echo Ordner: %~dp0
echo.

if exist "pomodoro.py" (echo [ok]     pomodoro.py gefunden) else (echo [FEHLER] pomodoro.py fehlt in diesem Ordner)

set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY (where python >nul 2>nul && set "PY=python")

if not defined PY (
    echo [FEHLER] Kein Python im PATH gefunden.
    echo          https://www.python.org/downloads/ - beim Setup
    echo          "Add python.exe to PATH" anhaken.
    echo.
    pause
    exit /b 1
)

echo [ok]     Python-Starter: %PY%
for /f "delims=" %%v in ('%PY% -c "import sys; print(sys.version.split()[0])" 2^>^&1') do echo [ok]     Python-Version: %%v
for /f "delims=" %%v in ('%PY% -c "import sys; print(sys.executable)" 2^>^&1') do echo [ok]     Pfad: %%v

%PY% -c "import tkinter; print('[ok]     Tkinter:', tkinter.TkVersion)" 2>&1
if errorlevel 1 (
    echo [FEHLER] Tkinter fehlt. Einstellungen ^> Apps ^> Python ^> Aendern ^> Modify
    echo          und dabei "tcl/tk and IDLE" anhaken.
    echo.
    pause
    exit /b 1
)

echo.
echo Starte den Timer... Fehlermeldungen erscheinen hier unten.
echo (Zum Beenden das Timer-Fenster schliessen oder Strg+C druecken.)
echo ------------------------------------------------------------
%PY% "pomodoro.py"
echo ------------------------------------------------------------
echo Beendet mit Code %errorlevel%.
echo.
echo Falls oben ein Fehler steht: Text markieren, mit Enter kopieren und mir schicken.
pause
