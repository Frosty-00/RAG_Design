<#
self-rag — single-script launcher (PowerShell)

Phases:
  1. Verify Docker / Anaconda / Node, auto-install missing deps on first run
  2. Bring up Docker (milvus / etcd / minio / redis)
  3. Launch FastAPI / Celery worker / Vite as 3 minimized cmd windows
  4. Open the browser
  5. Wait at a prompt; on [S] cleanly stop everything, on [R] restart apps

Closing this window will NOT kill the children (they run in their own
windows). Press [S] for clean shutdown.
#>

$ErrorActionPreference = 'Continue'   # we handle errors ourselves
Set-StrictMode -Version Latest

# ------------------------------------------------------------------ config

$ANACONDA_HOME = if ($env:ANACONDA_HOME) { $env:ANACONDA_HOME } else { 'D:\Anaconda' }
$CONDA_ENV     = if ($env:CONDA_ENV)     { $env:CONDA_ENV }     else { 'self_RAG_2' }
$NODE_HOME     = if ($env:NODE_HOME)     { $env:NODE_HOME }     else { 'C:\Program Files\nodejs' }
$REPO_ROOT     = $PSScriptRoot
$PYTHON_EXE    = Join-Path $ANACONDA_HOME "envs\$CONDA_ENV\python.exe"

Set-Location $REPO_ROOT

function Write-Step($msg) { Write-Host "  $msg" -ForegroundColor DarkGray }
function Write-Ok($msg)   { Write-Host "  ✔ $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  ✘ $msg" -ForegroundColor Red }

function Fatal($msg) {
    Write-Err $msg
    Write-Host ''
    Read-Host 'Press Enter to close'
    exit 1
}

# ------------------------------------------------------------------ phase 1

Write-Host ''
Write-Host '=== self-rag launcher ==========================================' -ForegroundColor Cyan
Write-Host "Repo: $REPO_ROOT"
Write-Host ''
Write-Host '[Phase 1/3] Environment...'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fatal 'Docker not on PATH. Install Docker Desktop: https://docs.docker.com/desktop/install/windows-install/'
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Fatal 'Docker daemon not running. Start Docker Desktop first.'
}
Write-Ok 'Docker'

if (-not (Test-Path "$ANACONDA_HOME\Scripts\conda.exe")) {
    Fatal "Anaconda not found at '$ANACONDA_HOME'. Set `$env:ANACONDA_HOME if installed elsewhere."
}
Write-Ok 'Anaconda'

if (-not (Test-Path "$NODE_HOME\node.exe") -and -not (Get-Command node -ErrorAction SilentlyContinue)) {
    Fatal 'Node.js not found. Install LTS: https://nodejs.org/'
}
Write-Ok 'Node'

# Conda env: create if missing
if (-not (Test-Path $PYTHON_EXE)) {
    Write-Step "Creating conda env '$CONDA_ENV' (one-time)..."
    & "$ANACONDA_HOME\Scripts\conda.exe" create -n $CONDA_ENV python=3.11 -y
    if ($LASTEXITCODE -ne 0) { Fatal 'conda create failed' }
}
Write-Ok "conda env $CONDA_ENV"

# Python deps: detect with `python -c "import app"`; install if missing
& $PYTHON_EXE -c "import app" *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Step 'Installing Python deps (first run only, ~2 min)...'
    & $PYTHON_EXE -m pip install -e ".[dev]"
    if ($LASTEXITCODE -ne 0) { Fatal 'pip install failed' }
}
Write-Ok 'Python deps'

# Frontend deps
if (-not (Test-Path 'frontend\node_modules')) {
    Write-Step 'Installing frontend deps (first run only)...'
    Push-Location frontend
    $env:Path = "$NODE_HOME;$env:Path"
    & npm install --no-audit --no-fund
    $rc = $LASTEXITCODE
    Pop-Location
    if ($rc -ne 0) { Fatal 'npm install failed' }
}
Write-Ok 'Frontend deps'

# .env: copy template on first run
if (-not (Test-Path '.env') -and (Test-Path '.env.example')) {
    Copy-Item '.env.example' '.env'
    Write-Ok '.env created from template'
}

# ------------------------------------------------------------------ phase 2

Write-Host ''
Write-Host '[Phase 2/3] Services...'

# Wipe any leftover RAG-* windows so port binds don't collide
foreach ($title in @('RAG-API*', 'RAG-WORKER*', 'RAG-WEB*')) {
    & taskkill /FI "WINDOWTITLE eq $title" /T /F *> $null
}

# Free orphan port owners (uvicorn process trees that survived a previous run)
foreach ($port in @(8000, 5173)) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object {
            Write-Step "Releasing port $port (pid $($_.OwningProcess))"
            & taskkill /PID $_.OwningProcess /T /F *> $null
        }
}

Write-Step 'Docker services...'
docker compose up -d milvus etcd minio redis *> $null
if ($LASTEXITCODE -ne 0) { Fatal 'docker compose up failed' }

Write-Step 'Waiting for Milvus health...'
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $r = Invoke-WebRequest 'http://localhost:9091/healthz' -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
}
if ($ok) { Write-Ok 'Milvus' } else { Write-Warn 'Milvus not healthy after 120s, continuing' }

# Launch app processes directly — no `cmd /k` middle layer. Some Windows
# Terminal configurations kill cmd /k right after spawn, so we run the
# real program (python / npm) as the top-level process and keep PID
# references for clean shutdown.

$Script:children = @()

# Note: do NOT name a parameter $args — PowerShell treats it as the function's
# automatic argument array and our caller's value gets shadowed. Use $argList.
function Start-App([string]$name, [string]$file, [string[]]$argList, [string]$cwd) {
    try {
        $p = Start-Process -FilePath $file -ArgumentList $argList `
            -WorkingDirectory $cwd -PassThru `
            -WindowStyle Minimized
        $Script:children += [pscustomobject]@{ Name = $name; Pid = $p.Id }
        Write-Ok "$name started (pid $($p.Id))"
    } catch {
        Write-Err "Failed to start $name : $_"
    }
}

# 1) FastAPI — direct python -m uvicorn
Start-App 'RAG-API' $PYTHON_EXE `
    @('-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8000','--reload') `
    $REPO_ROOT

# 2) Celery worker — direct python -m celery
Start-App 'RAG-WORKER' $PYTHON_EXE `
    @('-m','celery','-A','app.workers.celery_app','worker','-l','info','-P','solo') `
    $REPO_ROOT

# 3) Vite — npm.cmd is a .cmd shim; PowerShell can launch it directly
$NPM_CMD = Join-Path $NODE_HOME 'npm.cmd'
if (Test-Path $NPM_CMD) {
    Start-App 'RAG-WEB' $NPM_CMD @('run','dev') (Join-Path $REPO_ROOT 'frontend')
} else {
    # Fallback: rely on npm on PATH
    Start-App 'RAG-WEB' 'npm' @('run','dev') (Join-Path $REPO_ROOT 'frontend')
}

Write-Step 'Waiting for frontend (up to 90s)...'
$ok = $false
for ($i = 0; $i -lt 90; $i++) {
    try {
        $r = Invoke-WebRequest 'http://localhost:5173' -UseBasicParsing -TimeoutSec 1 -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
    Start-Sleep -Seconds 1
}
if ($ok) {
    Write-Ok 'Frontend ready'
    Start-Process 'http://localhost:5173'
} else {
    Write-Warn 'Frontend not reachable in 90s; click RAG-WEB on the taskbar to debug'
}

# ------------------------------------------------------------------ phase 3

Write-Host ''
Write-Host '=== self-rag is running ========================================' -ForegroundColor Cyan
Write-Host '  Frontend : http://localhost:5173'
Write-Host '  Backend  : http://localhost:8000'
Write-Host '  MinIO UI : http://localhost:9001  (minioadmin / minioadmin)'
Write-Host ''
Write-Host 'Three minimized windows on the taskbar:'
Write-Host '  RAG-API  RAG-WORKER  RAG-WEB    (click to view live logs)'
Write-Host ''

function Stop-Children {
    # 1. Kill the children we tracked (clean path)
    foreach ($c in $Script:children) {
        try {
            & taskkill /PID $c.Pid /T /F *> $null
            Write-Step "Stopped $($c.Name) (pid $($c.Pid))"
        } catch { }
    }
    $Script:children = @()
    # 2. Belt-and-suspenders: any stray worker / uvicorn / vite from a
    #    previous unclean run (e.g. another `run.bat` window still open)
    #    will keep loading old code; sweep them too.
    Get-WmiObject Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'celery|uvicorn' } |
        ForEach-Object { & taskkill /PID $_.ProcessId /T /F *> $null }
    Start-Sleep -Milliseconds 500
}

function Show-Status {
    Write-Host ''
    foreach ($c in $Script:children) {
        $alive = $null -ne (Get-Process -Id $c.Pid -ErrorAction SilentlyContinue)
        $tag = if ($alive) { '✔ alive' } else { '✘ DEAD' }
        $color = if ($alive) { 'Green' } else { 'Red' }
        Write-Host ("  {0,-12} pid={1,-6} {2}" -f $c.Name, $c.Pid, $tag) -ForegroundColor $color
    }
}

while ($true) {
    Show-Status
    Write-Host ''
    $key = Read-Host '[S]top everything | [R]estart app processes | [L]ist status'
    switch ($key.ToUpper()) {
        'S' {
            Write-Host ''
            Write-Host '[Phase 3/3] Stopping...'
            Stop-Children
            Write-Step 'Docker (volumes preserved)...'
            docker compose stop *> $null
            Write-Ok 'All stopped. Bye.'
            Start-Sleep -Seconds 1
            exit 0
        }
        'R' {
            Write-Host '  Restarting app processes...'
            Stop-Children
            Start-Sleep -Seconds 2
            Start-App 'RAG-API' $PYTHON_EXE `
                @('-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8000','--reload') `
                $REPO_ROOT
            Start-App 'RAG-WORKER' $PYTHON_EXE `
                @('-m','celery','-A','app.workers.celery_app','worker','-l','info','-P','solo') `
                $REPO_ROOT
            $NPM_CMD = Join-Path $NODE_HOME 'npm.cmd'
            $webExe = if (Test-Path $NPM_CMD) { $NPM_CMD } else { 'npm' }
            Start-App 'RAG-WEB' $webExe @('run','dev') (Join-Path $REPO_ROOT 'frontend')
            Write-Ok 'Restarted'
        }
        'L' { }
        default { Write-Warn 'Unknown choice. Press S, R, or L.' }
    }
}
