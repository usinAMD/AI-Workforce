@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
 echo Install Python 3.10+ and enable Add Python to PATH.
 pause
 exit /b 1
)
python --version
python server.py
pause
