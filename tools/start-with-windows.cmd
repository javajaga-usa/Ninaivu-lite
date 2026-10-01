@echo off
rem Start Ninaivu Lite quietly (no window) whenever you sign in to Windows.
rem   tools\start-with-windows.cmd        turn it on
rem   tools\start-with-windows.cmd off    turn it off
rem Start it once with start.cmd first, so everything is installed.
chcp 65001 >nul
setlocal
set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Ninaivu Lite.vbs"

if /i "%~1"=="off" (
  del "%LINK%" >nul 2>&1
  echo   Ninaivu Lite will no longer start with Windows.
  echo   நினைவு லைட் இனி Windows உடன் தொடங்காது.
  pause
  exit /b 0
)

if not exist "%ROOT%\.venv\Scripts\pythonw.exe" (
  echo   Please start Ninaivu Lite once with start.cmd first.
  echo   முதலில் start.cmd மூலம் நினைவு லைட்டை ஒருமுறை தொடங்கவும்.
  pause
  exit /b 1
)

> "%LINK%" echo Set shell = CreateObject("WScript.Shell")
>> "%LINK%" echo shell.CurrentDirectory = "%ROOT%"
>> "%LINK%" echo shell.Run """%ROOT%\.venv\Scripts\pythonw.exe"" -m ninaivu_lite --no-browser", 0, False

echo   Done: Ninaivu Lite will start by itself when you sign in.
echo   முடிந்தது: நீங்கள் உள்நுழையும்போது நினைவு லைட் தானாகத் தொடங்கும்.
pause
endlocal
