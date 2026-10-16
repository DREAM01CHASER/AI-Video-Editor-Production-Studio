@echo off
setlocal enableextensions
title AI Video Production Studio

rem ---------------------------------------------------------------------------
rem  AI Video Production Studio - one-click launcher for Windows.
rem  Double-click this file to start the local server and open your browser.
rem  Press Ctrl+C in the window that opens to stop the app.
rem ---------------------------------------------------------------------------

rem Always run from the folder this file lives in (survives spaces in the path).
cd /d "%~dp0"

echo ============================================
echo   AI Video Production Studio
echo ============================================
echo.

rem --- Choose the interpreter: project venv first, then the py launcher, then python.
set "PYEXE=python"
set "PYARG="
if exist ".venv\Scripts\python.exe" (
    set "PYEXE=.venv\Scripts\python.exe"
) else (
    where py >nul 2>nul && set "PYEXE=py" && set "PYARG=-3"
)

rem --- If the server is already running, just open the browser and stop.
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }"
if not errorlevel 1 (
    echo The studio is already running. Opening the browser...
    start "" "http://127.0.0.1:8000/"
    echo.
    pause
    exit /b 0
)

echo Folder : %CD%
echo Python : %PYEXE% %PYARG%
echo URL    : http://127.0.0.1:8000/
echo.
echo Starting the server. Your browser opens by itself once it is ready.
echo Press Ctrl+C in this window to stop the app.
echo.

rem --- Open the browser in the background, as soon as the server answers.
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "$u='http://127.0.0.1:8000/api/health'; $b='http://127.0.0.1:8000/'; for ($i=0; $i -lt 120; $i++) { try { Invoke-WebRequest -UseBasicParsing -Uri $u -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep -Milliseconds 500 } }; Start-Process $b"

rem --- Run the server in this window (blocks until Ctrl+C).
"%PYEXE%" %PYARG% -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo The server has stopped.
pause
