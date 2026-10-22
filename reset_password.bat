@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [error] venv not found. run install.bat first
  pause
  exit /b 1
)
".venv\Scripts\python.exe" tools\reset_password.py %*
pause
