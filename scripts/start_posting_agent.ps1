# scripts\start_posting_agent.ps1
# Launched by Windows Task Scheduler ("VTX Posting Agent" task).
# Runs posting_agent.py --watch and appends stdout+stderr to logs\posting_agent.log.
# Log is rotated when it exceeds 10 MB (old log renamed with a timestamp).

$ROOT    = Split-Path $PSScriptRoot -Parent
$PYTHON  = "$ROOT\.venv\Scripts\python.exe"
$SCRIPT  = "$ROOT\scripts\posting_agent.py"
$LOGDIR  = "$ROOT\logs"
$LOGFILE = "$LOGDIR\posting_agent.log"
$MAXBYTES = 10 * 1024 * 1024   # 10 MB

if (-not (Test-Path $LOGDIR)) { New-Item -ItemType Directory -Path $LOGDIR | Out-Null }

# Rotate if the log is already large
if ((Test-Path $LOGFILE) -and (Get-Item $LOGFILE).Length -gt $MAXBYTES) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    Rename-Item $LOGFILE "$LOGDIR\posting_agent_$stamp.log"
}

$env:PYTHONUTF8        = "1"
$env:PYTHONIOENCODING  = "utf-8"

$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$stamp] posting_agent starting (PID will follow)" | Out-File -Append -Encoding utf8 $LOGFILE

& $PYTHON $SCRIPT --watch *>> $LOGFILE
