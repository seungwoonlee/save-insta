@echo off
setlocal
cd /d "%~dp0"

rem --- find Python ---
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY (
  echo Python 3.9+ is required. Install from https://www.python.org/downloads/
  pause
  exit /b 1
)

rem --- first run: create venv and install dependencies ---
if not exist ".venv\Scripts\python.exe" (
  echo [setup] creating virtual environment...
  %PY% -m venv .venv || goto :fail
)
if not exist ".venv\.deps_ok" (
  echo [setup] installing dependencies...
  ".venv\Scripts\python.exe" -m pip install --quiet flask instaloader requests || goto :fail
  echo ok> ".venv\.deps_ok"
)

rem --- run (opens the browser automatically) ---
".venv\Scripts\python.exe" save_insta.py
goto :eof

:fail
echo Setup failed.
pause
exit /b 1
