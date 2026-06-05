# scripts/deploy_dashboard.ps1
# Deploy the AcumenAI dashboard JSON API (dashboard/app.py) to Cloud Run.
#
# Run from project root after: gcloud auth login && gcloud auth application-default login
#
# What this does (idempotent):
#   1. Enables run + cloudbuild + artifactregistry APIs
#   2. Creates a dedicated runtime service account
#   3. Grants minimum BigQuery IAM (job user + data editor for approval DML)
#   4. Deploys the Cloud Run service from the root Dockerfile (gcloud run deploy --source .)
#
# Auth: the API validates the orchelix.com identity-provider JWT (Clerk recommended)
# against its public JWKS. These are public identifiers, NOT secrets. Pass them in;
# health + /api/demo/run work even before they are set (only /api/live/* need them).
#
# NOTE: keep this file ASCII-only. Windows PowerShell 5.1 mis-parses non-ASCII
# characters (em-dash, smart quotes) in files saved without a BOM.

param(
    [string]$JwksUrl    = "",
    [string]$Issuer     = "",
    [string]$Audience   = "",
    [string]$CorsOrigin = "https://orchelix.com,https://www.orchelix.com"
)

$PROJECT  = "vtx-accounting-os-prod"
$REGION   = "northamerica-northeast2"
$SERVICE  = "acumenai-api"
$SA_NAME  = "vtx-dashboard-api-sa"
$SA_EMAIL = "$SA_NAME@$PROJECT.iam.gserviceaccount.com"

Write-Host "=== AcumenAI Dashboard API - Cloud Run Deployment ===" -ForegroundColor Cyan
Write-Host "Project : $PROJECT"
Write-Host "Region  : $REGION"
Write-Host "Service : $SERVICE"
Write-Host "CORS    : $CorsOrigin"
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Enable APIs
# ---------------------------------------------------------------------------
Write-Host "1/4  Enabling APIs (run, cloudbuild, artifactregistry) ..." -ForegroundColor Yellow
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com --project=$PROJECT

# ---------------------------------------------------------------------------
# 2. Service account
# ---------------------------------------------------------------------------
Write-Host "2/4  Creating service account $SA_NAME ..." -ForegroundColor Yellow
gcloud iam service-accounts create $SA_NAME --display-name="AcumenAI Dashboard API" --project=$PROJECT
if (-not $?) { Write-Host "     (may already exist - continuing)" }

# ---------------------------------------------------------------------------
# 3. IAM bindings (minimum needed)
# ---------------------------------------------------------------------------
Write-Host "3/4  Granting IAM roles ..." -ForegroundColor Yellow
$ROLES = @(
    "roles/bigquery.jobUser",
    "roles/bigquery.dataEditor"
)
foreach ($role in $ROLES) {
    Write-Host "     $role"
    gcloud projects add-iam-policy-binding $PROJECT --member="serviceAccount:$SA_EMAIL" --role=$role --quiet | Out-Null
}

# ---------------------------------------------------------------------------
# 4. Deploy the Cloud Run service (root Dockerfile, --source .)
# ---------------------------------------------------------------------------
Write-Host "4/4  Deploying Cloud Run service $SERVICE ..." -ForegroundColor Yellow

# Use gcloud's alternate-delimiter syntax (^@^) because CORS_ORIGIN may itself
# contain commas (multiple origins) and gcloud splits env-var pairs on commas.
$envVars = "^@^GOOGLE_CLOUD_PROJECT=$PROJECT@BQ_LOCATION=$REGION@CORS_ORIGIN=$CorsOrigin"
if ($JwksUrl)  { $envVars = "$envVars@AUTH_JWKS_URL=$JwksUrl" }
if ($Issuer)   { $envVars = "$envVars@AUTH_ISSUER=$Issuer" }
if ($Audience) { $envVars = "$envVars@AUTH_AUDIENCE=$Audience" }

if (-not $JwksUrl) {
    Write-Host "     NOTE: AUTH_JWKS_URL not set - /api/live/* will reject all tokens (401)." -ForegroundColor DarkYellow
    Write-Host "           Re-run with -JwksUrl and -Issuer once Clerk is wired." -ForegroundColor DarkYellow
}

gcloud run deploy $SERVICE `
    --source=. `
    --region=$REGION `
    --service-account=$SA_EMAIL `
    --allow-unauthenticated `
    --set-env-vars=$envVars `
    --memory=1Gi `
    --cpu=1 `
    --min-instances=0 `
    --max-instances=5 `
    --timeout=120 `
    --project=$PROJECT `
    --quiet

if ($?) {
    $url = gcloud run services describe $SERVICE --region=$REGION --project=$PROJECT --format="value(status.url)"
    Write-Host ""
    Write-Host "=== Deployment complete ===" -ForegroundColor Green
    Write-Host "Service URL: $url"
    Write-Host ""
    Write-Host "Smoke-check (public):"
    Write-Host "  curl $url/api/health"
    Write-Host "  curl $url/api/demo/run"
    Write-Host ""
    Write-Host "Set NEXT_PUBLIC_ACUMEN_API_BASE in orchelix.com to: $url"
    Write-Host "View logs:"
    Write-Host "  gcloud run services logs read $SERVICE --region=$REGION --limit=50"
} else {
    Write-Host "ERROR: Cloud Run deploy failed." -ForegroundColor Red
    exit 1
}
