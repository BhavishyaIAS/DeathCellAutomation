@echo off
rem Starts the Death Claims Cell application for the office network.
cd /d "%~dp0"
title Death Claims Cell - keep this window open

if not exist ".venv\Scripts\python.exe" (
  echo The application is not set up yet. Run setup.bat first.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" serve.py
echo.
echo The application has stopped.
pause
