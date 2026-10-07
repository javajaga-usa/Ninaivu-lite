@echo off
rem Start Ninaivu Lite quietly (no window) whenever you sign in to Windows.
rem   tools\start-with-windows.cmd        turn it on
rem   tools\start-with-windows.cmd off    turn it off
rem Start it once with start.cmd first, so everything is installed.
chcp 65001 >nul
setlocal
set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
rem This copy's own name; the installed and portable ones have theirs (ninaivu_lite\control.py).
set "LINK=%STARTUP%\Ninaivu Lite (source).vbs"
rem Before 1.6.0 every copy used "Ninaivu Lite.vbs": taken away when it starts this one.
if exist "%STARTUP%\Ninaivu Lite.vbs" findstr /l /i /c:"%ROOT%" "%STARTUP%\Ninaivu Lite.vbs" >nul 2>&1 && del "%STARTUP%\Ninaivu Lite.vbs" >nul 2>&1

if /i "%~1"=="off" (
  del "%LINK%" >nul 2>&1
  echo   Ninaivu Lite will no longer start with Windows.
  pause
  exit /b 0
)

if not exist "%ROOT%\.venv\Scripts\pythonw.exe" (
  echo   Please start Ninaivu Lite once with start.cmd first.
  pause
  exit /b 1
)

rem Written by Ninaivu Lite itself: in UTF-16, which Windows Script Host reads
rem whatever letters the folder names hold (echo here wrote UTF-8, read as ANSI).
pushd "%ROOT%"
"%ROOT%\.venv\Scripts\python.exe" -m ninaivu_lite.control --autostart on
set "RESULT=%ERRORLEVEL%"
popd
if not "%RESULT%"=="0" (
  echo   Could not set it up. See the message above.
  pause
  exit /b 1
)

echo   Done: Ninaivu Lite will start by itself when you sign in.
pause
endlocal
