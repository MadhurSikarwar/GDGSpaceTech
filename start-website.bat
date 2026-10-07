@echo off
rem ---------------------------------------------------------------------------
rem  OrbitWatch: double-click to start the website.
rem    1. makes sure MySQL (a Windows service) is running
rem    2. starts the MongoDB cluster (ow mongo-start; members already up are skipped)
rem    3. starts the HTTPS website and the scheduler (ow service) in this window
rem    4. opens https://localhost:8443/ in your browser once the site answers
rem  Close this window or press Ctrl+C to stop the website. MongoDB keeps running;
rem  stop it with:  ow mongo-stop
rem  Optional: set ORBITWATCH_NO_BROWSER=1 to skip opening the browser.
rem ---------------------------------------------------------------------------
setlocal
title OrbitWatch
cd /d "%~dp0"
if "%ORBITWATCH_RUNTIME%"=="" set "ORBITWATCH_RUNTIME=%USERPROFILE%\orbitwatch-runtime"
if "%MYSQL_SERVICE%"=="" set "MYSQL_SERVICE=MySQL84"
set "URL=https://localhost:8443/"

if not exist "%ORBITWATCH_RUNTIME%\venv\Scripts\python.exe" (
  echo [OrbitWatch] The Python environment was not found in %ORBITWATCH_RUNTIME%\venv
  echo              Follow "Setup - Windows" in README.md first.
  pause
  exit /b 1
)

rem --- already running? then only open it
netstat -ano | findstr /R /C:":8443 .*LISTENING" >nul
if not errorlevel 1 (
  echo [OrbitWatch] The website is already running at %URL%
  if not defined ORBITWATCH_NO_BROWSER start "" "%URL%"
  ping -n 4 127.0.0.1 >nul
  exit /b 0
)

rem --- MySQL: a Windows service that normally starts with Windows
netstat -ano | findstr /R /C:":3306 .*LISTENING" >nul
if errorlevel 1 (
  echo [OrbitWatch] MySQL is not listening on port 3306, starting the service %MYSQL_SERVICE% ...
  net start %MYSQL_SERVICE% >nul 2>&1
  ping -n 6 127.0.0.1 >nul
  netstat -ano | findstr /R /C:":3306 .*LISTENING" >nul
  if errorlevel 1 (
    echo [OrbitWatch] MySQL could not be started. Start it from services.msc, or run this file as administrator.
    pause
    exit /b 1
  )
)

rem --- MongoDB cluster
echo [OrbitWatch] Starting the MongoDB cluster ...
call "%~dp0ow.cmd" mongo-start >"%TEMP%\orbitwatch-mongo-start.log" 2>&1
if errorlevel 1 (
  echo [OrbitWatch] MongoDB did not start. Details: %TEMP%\orbitwatch-mongo-start.log
  pause
  exit /b 1
)

rem --- browser: a hidden helper waits until the site accepts connections, then opens it
if not defined ORBITWATCH_NO_BROWSER start "" /min powershell -NoProfile -WindowStyle Hidden -Command "for ($i = 0; $i -lt 90; $i++) { try { $c = New-Object Net.Sockets.TcpClient -ArgumentList '127.0.0.1', 8443; $c.Close(); Start-Process 'https://localhost:8443/'; break } catch { Start-Sleep -Seconds 1 } }"

echo.
echo [OrbitWatch] Starting the website and the scheduler at %URL%
echo              The first time, the browser warns about the local certificate:
echo              choose Advanced, then Continue to localhost.
echo              Close this window or press Ctrl+C to stop the website.
echo.
call "%~dp0ow.cmd" service
echo.
echo [OrbitWatch] The website has stopped. MongoDB is still running ^(ow mongo-stop stops it^).
pause
