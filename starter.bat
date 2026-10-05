@echo off
rem ===========================================================
rem  CodexAstra - Doppelklick-Starter (kein Terminal-Fenster)
rem -----------------------------------------------------------
rem  Startet die App ueber pythonw.exe (also ohne Konsole).
rem  Fehlt die tkinter-faehige .venv, wird einmalig run.ps1
rem  aufgerufen, das sie anlegt und danach die App startet.
rem ===========================================================
setlocal
set "BASE=%~dp0"
set "PYW=%BASE%.venv\Scripts\pythonw.exe"
set "APP=%BASE%app.py"

if not exist "%APP%" echo app.py fehlt: "%APP%"
if not exist "%APP%" pause
if not exist "%APP%" exit /b 1

if exist "%PYW%" start "" "%PYW%" "%APP%"
if not exist "%PYW%" start "" /min powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%BASE%run.ps1"

endlocal
exit /b 0
