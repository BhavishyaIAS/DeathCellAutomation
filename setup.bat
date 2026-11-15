@echo off
rem One-time setup of the Death Claims Cell application on this computer.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed. Install Python 3.11 or later from python.org
  echo and tick "Add python.exe to PATH" during installation. Then run this again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating a private Python environment...
  python -m venv .venv || goto :failed
)

echo Installing the required packages (needs internet this one time)...
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt || goto :failed

echo Preparing the database...
".venv\Scripts\python.exe" manage.py migrate --verbosity 0 || goto :failed
".venv\Scripts\python.exe" manage.py seed_demo --masters || goto :failed

echo.
echo Setup is complete. Double-click start.bat to run the application.
echo Sign in as "nodal" first and change the passwords of all users.
pause
exit /b 0

:failed
echo.
echo Setup did not finish. Please note the message above.
pause
exit /b 1
