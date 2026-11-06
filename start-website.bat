@echo off
rem ---------------------------------------------------------------------------
rem  OrbitWatch: double-click to start the website, also on a fresh computer.
rem    1. creates the Python environment and installs the packages (first start,
rem       and again whenever requirements.txt changes)
rem    2. checks that the MongoDB binaries are installed and that .env exists
rem    3. makes sure MySQL (a Windows service) is running
rem    4. first start: runs the first-time setup (ow setup) and asks for an
rem       administrator account (ow create-admin)
rem       later starts: applies database updates when the SQL files changed
rem       (ow prepare; existing data is kept)
rem    5. starts the MongoDB cluster (ow mongo-start; members already up are skipped)
rem    6. starts the HTTPS website and the scheduler (ow service) in this window
rem    7. opens https://localhost:8443/ in your browser once the site answers
rem  Close this window or press Ctrl+C to stop the website. MongoDB keeps running;
rem  stop it with:  ow mongo-stop
rem  Needs once, by hand: MySQL Server 8.4 (as a Windows service), Python 3.12 and
rem  the MongoDB 8.0 zip unpacked into %USERPROFILE%\orbitwatch-runtime\mongodb.
rem  Optional: set ORBITWATCH_NO_BROWSER=1 to skip opening the browser.
rem  Optional: set ORBITWATCH_CHECK_ONLY=1 to run steps 1 to 4 (check and prepare everything) without starting.
rem  Optional: set ORBITWATCH_STOP_MONGO=1 to stop the MongoDB cluster too when the website stops
rem            (a fully cold start next time; MySQL is a Windows service and is left running).
rem ---------------------------------------------------------------------------
setlocal EnableExtensions
title OrbitWatch
cd /d "%~dp0"
if "%ORBITWATCH_RUNTIME%"=="" set "ORBITWATCH_RUNTIME=%USERPROFILE%\orbitwatch-runtime"
if "%MYSQL_SERVICE%"=="" set "MYSQL_SERVICE=MySQL84"
set "URL=https://localhost:8443/"
set "PY=%ORBITWATCH_RUNTIME%\venv\Scripts\python.exe"

rem --- already running? then only open it
rem     (ports are tested by really connecting: netstat can report a port that works as something other than LISTENING)
call :tcp 8443
if not errorlevel 1 (
  echo [OrbitWatch] The website is already running at %URL%
  echo              To restart it ^(for example after changing code^), close the window it runs in first.
  if not defined ORBITWATCH_NO_BROWSER start "" "%URL%"
  ping -n 4 127.0.0.1 >nul
  exit /b 0
)

rem --- 1. Python environment: created on the first start
if exist "%PY%" goto :packages
call :find_python
if not defined BASEPY (
  echo [OrbitWatch] Python 3.12 or newer was not found. Install it from https://www.python.org/downloads/
  echo              ^(tick "Add python.exe to PATH"^), then start this file again.
  pause
  exit /b 1
)
echo [OrbitWatch] Creating the Python environment in %ORBITWATCH_RUNTIME%\venv ...
if not exist "%ORBITWATCH_RUNTIME%" mkdir "%ORBITWATCH_RUNTIME%"
%BASEPY% -m venv "%ORBITWATCH_RUNTIME%\venv"
if not exist "%PY%" (
  echo [OrbitWatch] The Python environment could not be created.
  pause
  exit /b 1
)

:packages
rem --- 2. Python packages: installed again whenever requirements.txt changed
set "REQ_STAMP=%ORBITWATCH_RUNTIME%\venv\requirements.installed"
fc /b "requirements.txt" "%REQ_STAMP%" >nul 2>&1
if not errorlevel 1 goto :mongo_bin
echo [OrbitWatch] Installing the Python packages (requirements.txt) ...
"%PY%" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo [OrbitWatch] Installing the packages failed ^(see above^). Check the internet connection and start again.
  pause
  exit /b 1
)
copy /y "requirements.txt" "%REQ_STAMP%" >nul

:mongo_bin
rem --- 3. MongoDB binaries (unpacked by hand once: there is no installer to run)
dir /b /ad "%ORBITWATCH_RUNTIME%\mongodb\mongodb-win32-*" >nul 2>&1
if not errorlevel 1 goto :env
echo [OrbitWatch] MongoDB was not found in %ORBITWATCH_RUNTIME%\mongodb
echo              Download the MongoDB Community Server 8.0 ZIP for Windows (and mongosh) from
echo              https://www.mongodb.com/try/download/community and unpack it there, so that
echo              %ORBITWATCH_RUNTIME%\mongodb\mongodb-win32-...\bin\mongod.exe exists. Then start this file again.
pause
exit /b 1

:env
rem --- 4. .env (settings and secrets): started from the template on a fresh copy
if not exist ".env" (
  copy /y ".env.example" ".env" >nul
  echo [OrbitWatch] Created .env from .env.example. Optional settings ^(Space-Track, e-mail, Groq^) can be added there later.
)

rem --- 5. MySQL: a Windows service that normally starts with Windows
call :tcp 3306
if errorlevel 1 (
  echo [OrbitWatch] MySQL is not answering on port 3306, starting the service %MYSQL_SERVICE% ...
  net start %MYSQL_SERVICE% >nul 2>&1
  ping -n 6 127.0.0.1 >nul
  call :tcp 3306
  if errorlevel 1 (
    echo [OrbitWatch] MySQL could not be started. Start it from services.msc, or run this file as administrator.
    pause
    exit /b 1
  )
)

rem --- 6. Database: first-time setup, or the updates that came with new code
call "%~dp0ow.cmd" prepare
if errorlevel 3 goto :first_time
if errorlevel 1 (
  echo [OrbitWatch] The database is not ready ^(see above^).
  pause
  exit /b 1
)
goto :ready

:first_time
echo.
echo [OrbitWatch] First-time setup: certificate, MySQL database and accounts, MongoDB cluster,
echo              the satellite catalogue, the first orbits and a first screening.
echo              It needs the internet and takes about 10 to 30 minutes. Do not close this window.
echo.
call "%~dp0ow.cmd" setup
if errorlevel 1 (
  echo [OrbitWatch] The first-time setup failed ^(see above^). Fix the problem and start this file again:
  echo              the setup carries on from where it is, nothing is lost.
  pause
  exit /b 1
)
echo.
echo [OrbitWatch] Create the administrator account you will log in with:
call "%~dp0ow.cmd" create-admin
if errorlevel 1 (
  echo [OrbitWatch] The account was not created. Run  ow create-admin  later to try again.
)

:ready
if defined ORBITWATCH_CHECK_ONLY (
  echo [OrbitWatch] Everything is ready. ^(ORBITWATCH_CHECK_ONLY is set: the website was not started.^)
  exit /b 0
)

rem --- 7. MongoDB cluster
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
if "%ORBITWATCH_STOP_MONGO%"=="1" (
  echo [OrbitWatch] The website has stopped. Stopping the MongoDB cluster ...
  call "%~dp0ow.cmd" mongo-stop >nul 2>&1
) else (
  echo [OrbitWatch] The website has stopped. MongoDB is still running ^(ow mongo-stop stops it^).
)
pause
goto :eof

rem --- :tcp PORT  ->  errorlevel 0 when something accepts a connection on 127.0.0.1:PORT, else 1
:tcp
powershell -NoProfile -Command "try { $c = New-Object Net.Sockets.TcpClient('127.0.0.1', %~1); $c.Close(); exit 0 } catch { exit 1 }"
exit /b %errorlevel%

rem --- :find_python  ->  BASEPY = a command that runs Python 3.12 or newer (the py launcher, or python on PATH)
:find_python
set "BASEPY="
py -3.12 -c "" >nul 2>&1
if not errorlevel 1 (
  set "BASEPY=py -3.12"
  exit /b 0
)
python -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 12) else 1)" >nul 2>&1
if not errorlevel 1 set "BASEPY=python"
exit /b 0
