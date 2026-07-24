@echo off
rem Baut eine eigenstaendige Pomodoro.exe (dist\Pomodoro.exe).
cd /d "%~dp0"

echo Installiere/aktualisiere PyInstaller...
python -m pip install --upgrade pyinstaller
if errorlevel 1 goto error

echo.
echo Baue Pomodoro.exe ...
python -m PyInstaller --noconfirm --clean --onefile --windowed --name Pomodoro pomodoro.py
if errorlevel 1 goto error

echo.
echo Fertig: %~dp0dist\Pomodoro.exe
echo Die Datei laesst sich frei verschieben - z. B. auf den Desktop.
pause
exit /b

:error
echo.
echo Der Build ist fehlgeschlagen. Ist Python installiert und im PATH?
pause
