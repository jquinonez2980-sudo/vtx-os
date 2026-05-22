#!/usr/bin/env bash
# Phase 0 — Enable required GCP APIs
# Run after: gcloud auth login && gcloud config set project YOUR_PROJECT_ID

set -euo pipefail

PROJECT_ID="${1:-$(gcloud config get-value project)}"

if [[ -z "$PROJECT_ID" ]]; then
  echo "ERROR: No project ID. Pass it as arg or run: gcloud config set project YOUR_PROJECT_ID"
  exit 1
fi

echo "Enabling APIs for project: $PROJECT_ID"

gcloud services enable \
  aiplatform.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com \
  eventarc.googleapis.com \
  gmail.googleapis.com \
  documentai.googleapis.com \
  cloudresourcemanager.googleapis.com \
  iam.googleapis.com \
  secretmanager.googleapis.com \
  --project="$PROJECT_ID"

echo "All APIs enabled successfully."
