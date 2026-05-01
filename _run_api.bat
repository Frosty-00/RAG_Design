@echo off
title RAG-API
REM Helper: run FastAPI inside the conda env. Invoked by run.bat.
if not defined ANACONDA_HOME set "ANACONDA_HOME=D:\Anaconda"
if not defined CONDA_ENV     set "CONDA_ENV=self_RAG_2"
cd /d "%~dp0"
call "%ANACONDA_HOME%\Scripts\activate.bat" "%CONDA_ENV%"
if errorlevel 1 (
    echo Failed to activate conda env "%CONDA_ENV%". Run setup.bat first.
    pause
    exit /b 1
)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
pause
