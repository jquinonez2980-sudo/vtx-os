# Phase 0 — Local environment setup (Windows PowerShell)
# Run from C:\vtx-os after Python 3.11 and gcloud CLI are installed

param(
    [string]$ProjectId = ""
)

$ErrorActionPreference = "Stop"
$VtxRoot = "C:\vtx-os"

Write-Host "=== VTX-OS Phase 0: Local Environment Setup ===" -ForegroundColor Cyan

# --- Python venv ---
Write-Host "`n[1/4] Creating Python 3.11 virtual environment..." -ForegroundColor Yellow
if (-not (Test-Path "$VtxRoot\.venv")) {
    py -3.11 -m venv "$VtxRoot\.venv"
    Write-Host "  venv created at $VtxRoot\.venv" -ForegroundColor Green
} else {
    Write-Host "  venv already exists — skipping." -ForegroundColor Gray
}

# --- Activate and install deps ---
Write-Host "`n[2/4] Installing Python dependencies..." -ForegroundColor Yellow
& "$VtxRoot\.venv\Scripts\pip.exe" install --upgrade pip --quiet
& "$VtxRoot\.venv\Scripts\pip.exe" install -r "$VtxRoot\requirements.txt" --quiet
Write-Host "  Dependencies installed." -ForegroundColor Green

# --- gcloud auth check ---
Write-Host "`n[3/4] Checking gcloud authentication..." -ForegroundColor Yellow
$authList = gcloud auth list --format="value(account)" 2>&1
if ($authList -match "@") {
    Write-Host "  Authenticated as: $authList" -ForegroundColor Green
} else {
    Write-Host "  NOT authenticated. Run: gcloud auth login" -ForegroundColor Red
}

# --- Set project ---
Write-Host "`n[4/4] GCP project configuration..." -ForegroundColor Yellow
if ($ProjectId -ne "") {
    gcloud config set project $ProjectId
    $env:GOOGLE_CLOUD_PROJECT = $ProjectId
    Write-Host "  Project set to: $ProjectId" -ForegroundColor Green
} else {
    $current = gcloud config get-value project 2>&1
    if ($current -and $current -notmatch "unset") {
        Write-Host "  Current project: $current" -ForegroundColor Green
    } else {
        Write-Host "  No project set. Run: gcloud config set project YOUR_PROJECT_ID" -ForegroundColor Red
    }
}

Write-Host "`n=== Setup complete. Activate venv: .venv\Scripts\Activate.ps1 ===" -ForegroundColor Cyan
