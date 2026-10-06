@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (set "GK2MT_PY=py -3") else (set "GK2MT_PY=python")
%GK2MT_PY% -c "import paramiko, webview; from importlib.metadata import version; assert version('pywebview') == '6.2.1'" >nul 2>nul
if errorlevel 1 (
  echo Setting up GK2MT's Deck and download panel support...
  %GK2MT_PY% -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Setup failed. Check your internet connection and Python installation.
    pause
    exit /b 1
  )
)
%GK2MT_PY% app.py
if errorlevel 1 pause
