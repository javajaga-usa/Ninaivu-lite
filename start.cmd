@echo off
rem Ninaivu Lite for Windows: double-click this file, or drag a photo folder onto it.
rem The first start prepares everything (a minute or two); later starts are quick.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Ninaivu Lite

rem Find a real Python 3.10+. The "python" that the Microsoft Store puts on the
rem PATH only opens the Store, so each candidate is asked to prove itself.
set "PY="
rem The Python the installers carry (.python-version) first, when it is here.
py -3.13 -c "import sys" >nul 2>&1 && set "PY=py -3.13"
if not defined PY py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"

if not defined PY (
  echo.
  echo   Ninaivu Lite needs Python 3.10 or newer, and this computer does not have it.
  echo   Install it from python.org, tick "Add python.exe to PATH", then start again.
  echo.
  start "" "https://www.python.org/downloads/windows/"
  pause
  exit /b 1
)

%PY% "%~dp0launcher\start.py" %*
if errorlevel 1 (
  echo.
  echo   Ninaivu Lite stopped with a problem. The details are above.
  pause
)
endlocal
