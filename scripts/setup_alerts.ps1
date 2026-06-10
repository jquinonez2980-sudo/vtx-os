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
# NOTE: server-side --filter syntax is quote-fragile from PowerShell (caused
# duplicate policies on 2026-06-10) — list everything, match client-side.
function Find-EmailChannel {
    $lines = gcloud beta monitoring channels list --project=$PROJECT `
        --format="csv[no-heading](name,labels.email_address)"
    foreach ($l in $lines) {
        $parts = $l -split ","
        if ($parts.Count -ge 2 -and $parts[1].Trim() -eq $Email) { return $parts[0].Trim() }
    }
    return $null
}
$channel = Find-EmailChannel
if (-not $channel) {
    gcloud beta monitoring channels create `
        --display-name="VTX-OS alerts ($Email)" `
        --type=email `
        --channel-labels="email_address=$Email" `
        --project=$PROJECT --quiet
    $channel = Find-EmailChannel
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
if (-not $channel) {
    Write-Host "     WARNING: no email channel (gcloud beta component missing?)." -ForegroundColor DarkYellow
    Write-Host "              Policies will be created/updated WITHOUT notifications." -ForegroundColor DarkYellow
}
# Snapshot all policies once; match displayName client-side (see note above).
$allPolicies = gcloud alpha monitoring policies list --project=$PROJECT `
    --format="csv[no-heading](name,displayName)"
function Find-Policy([string]$dn) {
    foreach ($l in $allPolicies) {
        $idx = $l.IndexOf(",")
        if ($idx -gt 0 -and $l.Substring($idx + 1).Trim() -eq $dn) {
            return $l.Substring(0, $idx).Trim()
        }
    }
    return $null
}
foreach ($file in $policies) {
    $displayName = (Get-Content $file -Raw | ConvertFrom-Json).displayName
    $existing = Find-Policy $displayName
    if ($existing) {
        if ($channel) {
            # idempotent: ensure the email channel is bound to the existing policy
            $bound = gcloud alpha monitoring policies describe $existing --project=$PROJECT `
                --format="value(notificationChannels)"
            if ($bound -like "*$channel*") {
                Write-Host "     EXISTS (channel bound): $displayName"
            } else {
                gcloud alpha monitoring policies update $existing `
                    --add-notification-channels=$channel --project=$PROJECT --quiet | Out-Null
                if ($?) { Write-Host "     UPDATED (channel attached): $displayName" -ForegroundColor Green }
                else    { Write-Host "     FAILED to attach channel:  $displayName" -ForegroundColor Red }
            }
        } else {
            Write-Host "     EXISTS: $displayName"
        }
    } else {
        $createArgs = @("alpha", "monitoring", "policies", "create",
                        "--policy-from-file=$file", "--project=$PROJECT", "--quiet")
        if ($channel) { $createArgs += "--notification-channels=$channel" }
        gcloud @createArgs
        if ($?) { Write-Host "     CREATED: $displayName" -ForegroundColor Green }
        else    { Write-Host "     FAILED:  $displayName" -ForegroundColor Red }
    }
}

Write-Host ""
Write-Host "Done. View policies: https://console.cloud.google.com/monitoring/alerting?project=$PROJECT"
