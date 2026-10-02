@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-public.ps1"
if errorlevel 1 pause
