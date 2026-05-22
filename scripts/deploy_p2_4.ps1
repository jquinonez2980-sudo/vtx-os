# scripts/deploy_p2_4.ps1
# P2.4 — Deploy Eventarc trigger: GCS object.finalize -> OrchestratorAgent
#
# Run from project root after: gcloud auth login && gcloud auth application-default login
#
# What this script does:
#   1. Creates a dedicated service account for the Cloud Run function
#   2. Grants the minimum IAM roles needed (BQ, GCS, Secret Manager)
#   3. Deploys the Cloud Run function (Gen 2) from the project root
#   4. Grants the Eventarc service agent invoker rights on the function
#   5. Creates the Eventarc trigger on the vtx-exports bucket

$PROJECT  = "vtx-accounting-os-prod"
$REGION   = "northamerica-northeast2"
$BUCKET   = "vtx-accounting-os-prod-vtx-exports"
$FUNCTION = "vtx-gcs-ingest"
$SA_NAME  = "vtx-gcs-trigger-sa"
$SA_EMAIL = "$SA_NAME@$PROJECT.iam.gserviceaccount.com"

Write-Host "=== P2.4 Eventarc Trigger Deployment ===" -ForegroundColor Cyan
Write-Host "Project : $PROJECT"
Write-Host "Region  : $REGION"
Write-Host "Bucket  : $BUCKET"
Write-Host "Function: $FUNCTION"
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Service account
# ---------------------------------------------------------------------------
Write-Host "1/5  Creating service account $SA_NAME ..." -ForegroundColor Yellow
gcloud iam service-accounts create $SA_NAME `
    --display-name="VTX GCS Ingest Trigger" `
    --project=$PROJECT
# idempotent: ignore "already exists" error
if (-not $?) { Write-Host "     (may already exist — continuing)" }

# ---------------------------------------------------------------------------
# 2. IAM bindings
# ---------------------------------------------------------------------------
Write-Host "2/5  Granting IAM roles ..." -ForegroundColor Yellow
$ROLES = @(
    "roles/bigquery.dataEditor",     # stream rows to BQ
    "roles/bigquery.jobUser",        # run BQ jobs
    "roles/storage.objectAdmin",     # read + copy objects within the exports bucket
    "roles/secretmanager.secretAccessor"  # read secrets (Chat webhook, Gmail creds, etc.)
)
foreach ($role in $ROLES) {
    Write-Host "     $role"
    gcloud projects add-iam-policy-binding $PROJECT `
        --member="serviceAccount:$SA_EMAIL" `
        --role=$role `
        --quiet
}

# ---------------------------------------------------------------------------
# 3. Deploy Cloud Run function (Gen 2)
#    --source=. bundles the full project root (agents/, core/, models/, sage50/, functions/)
#    --entry-point discovers handle_gcs_finalize via main.py
# ---------------------------------------------------------------------------
Write-Host "3/5  Deploying Cloud Run function $FUNCTION ..." -ForegroundColor Yellow
gcloud functions deploy $FUNCTION `
    --gen2 `
    --runtime=python312 `
    --region=$REGION `
    --source=. `
    --entry-point=handle_gcs_finalize `
    --service-account=$SA_EMAIL `
    --set-env-vars="GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=$REGION,GOOGLE_GENAI_USE_VERTEXAI=TRUE" `
    --memory=512Mi `
    --timeout=300s `
    --min-instances=0 `
    --max-instances=10 `
    --project=$PROJECT

if (-not $?) {
    Write-Host "ERROR: function deploy failed." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# 4. Grant Eventarc service agent invoker role on the function
# ---------------------------------------------------------------------------
Write-Host "4/5  Granting Eventarc service agent invoker rights ..." -ForegroundColor Yellow
$PROJECT_NUMBER = gcloud projects describe $PROJECT --format="value(projectNumber)"
$EVENTARC_SA    = "service-$PROJECT_NUMBER@gcp-sa-eventarc.iam.gserviceaccount.com"

gcloud run services add-iam-policy-binding $FUNCTION `
    --region=$REGION `
    --member="serviceAccount:$EVENTARC_SA" `
    --role="roles/run.invoker" `
    --project=$PROJECT

# ---------------------------------------------------------------------------
# 5. Eventarc trigger: GCS object.finalize on the exports bucket
# ---------------------------------------------------------------------------
Write-Host "5/5  Creating Eventarc trigger ..." -ForegroundColor Yellow
gcloud eventarc triggers create vtx-gcs-ingest-trigger `
    --location=$REGION `
    --destination-run-function=$FUNCTION `
    --destination-run-region=$REGION `
    --event-filters="type=google.cloud.storage.object.v1.finalized" `
    --event-filters="bucket=$BUCKET" `
    --service-account=$SA_EMAIL `
    --project=$PROJECT

if ($?) {
    Write-Host ""
    Write-Host "=== P2.4 deployment complete ===" -ForegroundColor Green
    Write-Host "Upload a CSV to gs://$BUCKET/bank-statements/ or"
    Write-Host "gs://$BUCKET/sage50/raw/YYYY/MM/DD/{report_type}/ to trigger the pipeline."
    Write-Host ""
    Write-Host "View function logs:"
    Write-Host "  gcloud functions logs read $FUNCTION --gen2 --region=$REGION --limit=50"
    Write-Host ""
    Write-Host "View trigger:"
    Write-Host "  gcloud eventarc triggers describe vtx-gcs-ingest-trigger --location=$REGION"
} else {
    Write-Host "ERROR: Eventarc trigger creation failed." -ForegroundColor Red
    exit 1
}
