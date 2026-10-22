@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYEXE="
for %%P in (
  "%USERPROFILE%\miniforge3\python.exe"
  "%USERPROFILE%\anaconda3\python.exe"
  "%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe"
  "%USERPROFILE%\AppData\Local\Programs\Python\Python311\python.exe"
) do (
  if exist %%P if not defined PYEXE set "PYEXE=%%~fP"
)
if not defined PYEXE (
  where py >nul 2>&1 && set "PYEXE=py -3"
)
if not defined PYEXE (
  echo [error] Python not found. install Python 3.10+ first.
  pause
  exit /b 1
)
echo [1/3] creating venv ...
%PYEXE% -m venv .venv
if errorlevel 1 ( echo [error] venv failed & pause & exit /b 1 )
echo [2/3] installing dependencies ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 ( echo [error] pip install failed & pause & exit /b 1 )
echo [3/3] done.
echo.
echo next: run login.bat (login to AutoDL once), then start.bat
pause
