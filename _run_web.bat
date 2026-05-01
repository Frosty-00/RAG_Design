@echo off
title RAG-WEB
REM Helper: run Vite dev server. Adds Node.js to PATH explicitly because
REM `start "TITLE" cmd /k "set PATH=...; ..."` inline mangles paths that
REM contain spaces (e.g. "C:\Program Files\nodejs").
if not defined NODE_HOME set "NODE_HOME=C:\Program Files\nodejs"
set "PATH=%NODE_HOME%;%PATH%"
cd /d "%~dp0frontend"
where npm >nul 2>&1
if errorlevel 1 (
    echo npm not on PATH. Install Node.js LTS or set NODE_HOME correctly.
    pause
    exit /b 1
)
if not exist "node_modules" (
    echo node_modules missing. Run setup.bat first.
    pause
    exit /b 1
)
call npm run dev
pause
