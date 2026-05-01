@echo off
REM Single-line entry point — all logic lives in run.ps1.
REM Bypass execution policy so users don't need to set it system-wide.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
if errorlevel 1 pause
