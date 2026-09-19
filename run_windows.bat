@echo off
title Industrial Safety AI
cd /d "%~dp0"
echo ============================================
echo   INDUSTRIAL SAFETY AI - WINDOWS STARTER
echo ============================================
where py >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.10-3.12 and try again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  py -m venv .venv
)
echo Installing/updating dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
echo.
echo Starting application...
echo Open http://127.0.0.1:5000 in your browser.
echo Close this window or press Ctrl+C to stop.
".venv\Scripts\python.exe" app.py
pause
