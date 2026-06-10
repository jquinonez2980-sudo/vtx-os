# scripts/setup_alerts.ps1
# Create Cloud Monitoring alert policies for VTX-OS (idempotent by displayName).
#   1. AcumenAI API - 5xx error rate         (config/monitoring/alert_cloud_run_5xx.json)
#   2. VTX-OS - application ERROR logs       (config/monitoring/alert_error_logs.json)
# Notifications go to an email channel (created if missing).
#
# Run from project root: .\scripts\setup_alerts.ps1
# NOTE: keep this file ASCII-only (Windows PowerShell 5.1 parsing).

param(
    [string]$Email = "jquinonez2980@gmail.com"
)

$PROJECT = "vtx-accounting-os-prod"

Write-Host "=== VTX-OS Alert Policy Setup ===" -ForegroundColor Cyan

# ---------------------------------------------------------------------------
# 1. Email notification channel (create if missing)
# ---------------------------------------------------------------------------
Write-Host "1/2  Ensuring email notification channel for $Email ..." -ForegroundColor Yellow
$channel = gcloud beta monitoring channels list --project=$PROJECT `
    --filter="type=email AND labels.email_address='$Email'" `
    --format="value(name)" | Select-Object -First 1
if (-not $channel) {
    gcloud beta monitoring channels create `
        --display-name="VTX-OS alerts ($Email)" `
        --type=email `
        --channel-labels="email_address=$Email" `
        --project=$PROJECT --quiet
    $channel = gcloud beta monitoring channels list --project=$PROJECT `
        --filter="type=email AND labels.email_address='$Email'" `
        --format="value(name)" | Select-Object -First 1
}
Write-Host "     channel: $channel"

# ---------------------------------------------------------------------------
# 2. Alert policies (skip if a policy with the same displayName exists)
# ---------------------------------------------------------------------------
Write-Host "2/2  Creating alert policies ..." -ForegroundColor Yellow
$policies = @(
    "config\monitoring\alert_cloud_run_5xx.json",
    "config\monitoring\alert_error_logs.json"
)
foreach ($file in $policies) {
    $displayName = (Get-Content $file -Raw | ConvertFrom-Json).displayName
    $existing = gcloud alpha monitoring policies list --project=$PROJECT `
        --filter="displayName='$displayName'" --format="value(name)" | Select-Object -First 1
    if ($existing) {
        Write-Host "     EXISTS: $displayName"
    } else {
        gcloud alpha monitoring policies create `
            --policy-from-file=$file `
            --notification-channels=$channel `
            --project=$PROJECT --quiet
        if ($?) { Write-Host "     CREATED: $displayName" -ForegroundColor Green }
        else    { Write-Host "     FAILED:  $displayName" -ForegroundColor Red }
    }
}

Write-Host ""
Write-Host "Done. View policies: https://console.cloud.google.com/monitoring/alerting?project=$PROJECT"
