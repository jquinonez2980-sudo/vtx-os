"""
Upload Sage 50 CSV exports to GCS.

Drop-zone flow:
  local CSV  →  sage50/raw/YYYY-MM-DD/{report_type}/{filename}
  on success →  move to sage50/staging/ for the ingest agent
  on failure →  move to sage50/failed/ with an error sidecar
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from google.cloud import storage

BUCKET = os.environ.get("GCS_BUCKET_EXPORTS", "vtx-accounting-os-prod-vtx-exports")
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "vtx-accounting-os-prod")


class ReportType(str, Enum):
    GL_TRANSACTIONS = "gl_transactions"
    AR_INVOICES = "ar_invoices"
    AP_BILLS = "ap_bills"
    CHART_OF_ACCOUNTS = "chart_of_accounts"
    CUSTOMERS = "customers"
    VENDORS = "vendors"
    TAX_SUMMARY = "tax_summary"       # GST/HST
    PAYROLL = "payroll"
    INVENTORY = "inventory"
    BANK_RECONCILIATION = "bank_reconciliation"


def upload_export(
    local_path: str | Path,
    report_type: ReportType,
    export_date: datetime | None = None,
    move_to_staging: bool = True,
) -> str:
    """Upload a local Sage 50 CSV export to GCS and optionally queue it for staging.

    Returns the GCS URI of the uploaded raw file.
    """
    local_path = Path(local_path)
    if not local_path.exists():
        raise FileNotFoundError(local_path)

    export_date = export_date or datetime.now(timezone.utc)
    date_prefix = export_date.strftime("%Y/%m/%d")
    blob_name = f"sage50/raw/{date_prefix}/{report_type.value}/{local_path.name}"

    client = storage.Client(project=PROJECT)
    bucket = client.bucket(BUCKET)

    blob = bucket.blob(blob_name)
    blob.metadata = {
        "report_type": report_type.value,
        "export_date": export_date.isoformat(),
        "source_file": local_path.name,
        "upload_id": str(uuid.uuid4()),
    }
    blob.upload_from_filename(str(local_path), content_type="text/csv")
    raw_uri = f"gs://{BUCKET}/{blob_name}"

    if move_to_staging:
        _copy_to_staging(bucket, blob_name, report_type, export_date)

    return raw_uri


def _copy_to_staging(
    bucket: storage.Bucket,
    raw_blob_name: str,
    report_type: ReportType,
    export_date: datetime,
) -> str:
    """Copy the raw blob into staging so the ingest agent can pick it up."""
    date_prefix = export_date.strftime("%Y/%m/%d")
    filename = Path(raw_blob_name).name
    staging_name = f"sage50/staging/{date_prefix}/{report_type.value}/{filename}"

    raw_blob = bucket.blob(raw_blob_name)
    bucket.copy_blob(raw_blob, bucket, staging_name)
    return f"gs://{BUCKET}/{staging_name}"


def move_to_archive(gcs_uri: str) -> str:
    """Move a staged file to archive after successful BigQuery load."""
    blob_name = gcs_uri.removeprefix(f"gs://{BUCKET}/")
    archive_name = blob_name.replace("sage50/staging/", "sage50/archive/", 1)

    client = storage.Client(project=PROJECT)
    bucket = client.bucket(BUCKET)
    src = bucket.blob(blob_name)
    bucket.copy_blob(src, bucket, archive_name)
    src.delete()
    return f"gs://{BUCKET}/{archive_name}"


def move_to_failed(gcs_uri: str, error: str) -> str:
    """Move a staged file to failed/ and write an error sidecar."""
    blob_name = gcs_uri.removeprefix(f"gs://{BUCKET}/")
    failed_name = blob_name.replace("sage50/staging/", "sage50/failed/", 1)

    client = storage.Client(project=PROJECT)
    bucket = client.bucket(BUCKET)
    src = bucket.blob(blob_name)
    bucket.copy_blob(src, bucket, failed_name)
    src.delete()

    sidecar = bucket.blob(failed_name + ".error.txt")
    sidecar.upload_from_string(error, content_type="text/plain")
    return f"gs://{BUCKET}/{failed_name}"
