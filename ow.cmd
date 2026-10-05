@echo off
rem OrbitWatch launcher: runs manage.py with the project's Python 3.12 environment.
if "%ORBITWATCH_RUNTIME%"=="" set "ORBITWATCH_RUNTIME=%USERPROFILE%\orbitwatch-runtime"
"%ORBITWATCH_RUNTIME%\venv\Scripts\python.exe" "%~dp0manage.py" %*
