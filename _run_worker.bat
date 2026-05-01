@echo off
title RAG-WORKER
REM Helper: run Celery worker inside the conda env.
if not defined ANACONDA_HOME set "ANACONDA_HOME=D:\Anaconda"
if not defined CONDA_ENV     set "CONDA_ENV=self_RAG_2"
cd /d "%~dp0"
call "%ANACONDA_HOME%\Scripts\activate.bat" "%CONDA_ENV%"
if errorlevel 1 (
    echo Failed to activate conda env "%CONDA_ENV%". Run setup.bat first.
    pause
    exit /b 1
)
celery -A app.workers.celery_app worker -l info -P solo
pause
