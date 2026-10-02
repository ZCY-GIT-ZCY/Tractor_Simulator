@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
if exist "C:\Miniconda\python.exe" (
  "C:\Miniconda\python.exe" -m tractor_sim %*
) else (
  python -m tractor_sim %*
)
if errorlevel 1 pause
