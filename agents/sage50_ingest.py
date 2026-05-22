"""
Sage50IngestAgent — uploads a local Sage 50 CSV export to GCS, or processes one
that has already landed there (Eventarc trigger path).

Expected payload keys — local-upload path:
    local_path   (str, required)  — absolute path to the CSV file on this machine
    report_type  (str, required)  — one of the ReportType enum values
    export_date  (str, optional)  — ISO date "YYYY-MM-DD"; defaults to today (UTC)
    to_staging   (bool, optional) — copy to staging/ after raw upload; default True

Expected payload keys — GCS-trigger path (file already in GCS raw/):
    gcs_uri      (str, required)  — gs://bucket/sage50/raw/YYYY/MM/DD/{report_type}/file.csv
    report_type  (str, required)  — one of the ReportType enum values
    to_staging   (bool, optional) — server-side copy to staging/; default True

Returns output keys:
    raw_gcs_uri      — gs://... path of the raw file (uploaded or already there)
    staging_gcs_uri  — gs://... path of the staging copy (if to_staging=True)
    report_type      — echoed back
    row_count        — number of data rows in the CSV (header excluded)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agents.base import AgentBase, TaskRequest, TaskResult, TaskType
from models.base import EventStatus
from sage50.csv_uploader import ReportType, upload_export


class Sage50IngestAgent(AgentBase):
    agent_id = "sage50-ingest-agent"

    def handle(self, request: TaskRequest) -> TaskResult:
        payload = request.payload
        report_type = ReportType(payload["report_type"])
        to_staging: bool = payload.get("to_staging", True)

        if gcs_uri := payload.get("gcs_uri"):
            # --- GCS-trigger path: file already in GCS raw/ ---
            raw_uri = gcs_uri
            row_count = _count_rows_from_gcs(gcs_uri, request.session_id)
            staging_uri = _copy_raw_to_staging(gcs_uri, report_type) if to_staging else None
        else:
            # --- Local-upload path: original behaviour ---
            local_path = Path(payload["local_path"])

            export_date: datetime | None = None
            if raw_date := payload.get("export_date"):
                export_date = datetime.fromisoformat(raw_date).replace(tzinfo=timezone.utc)

            raw_uri = upload_export(
                local_path=local_path,
                report_type=report_type,
                export_date=export_date,
                move_to_staging=to_staging,
            )

            with open(local_path, encoding="utf-8-sig") as fh:
                row_count = sum(1 for _ in fh) - 1

            staging_uri = raw_uri.replace("/raw/", "/staging/") if to_staging else None

        output: dict = {
            "raw_gcs_uri": raw_uri,
            "report_type": report_type.value,
            "row_count": row_count,
        }
        if staging_uri:
            output["staging_gcs_uri"] = staging_uri

        return TaskResult(
            task_id=request.task_id,
            task_type=TaskType.INGEST_SAGE50_CSV,
            agent_id=self.agent_id,
            status=EventStatus.SUCCESS,
            output=output,
        )


# ---------------------------------------------------------------------------
# Helpers for the GCS-trigger path
# ---------------------------------------------------------------------------

def _count_rows_from_gcs(gcs_uri: str, session_id: str) -> int:
    """Download a GCS CSV to a temp file, count data rows (header excluded), delete temp."""
    import tempfile
    from google.cloud import storage as gcs_lib

    bucket_name, blob_name = gcs_uri[5:].split("/", 1)
    tmp = Path(tempfile.mktemp(suffix=".csv", prefix=f"vtx_{session_id}_"))
    try:
        gcs_lib.Client().bucket(bucket_name).blob(blob_name).download_to_filename(str(tmp))
        with open(tmp, encoding="utf-8-sig") as fh:
            return sum(1 for _ in fh) - 1
    finally:
        tmp.unlink(missing_ok=True)


def _copy_raw_to_staging(gcs_uri: str, report_type: ReportType) -> str:
    """Server-side GCS copy from raw/ to staging/, returning the new URI."""
    from google.cloud import storage as gcs_lib
    from sage50.csv_uploader import BUCKET

    bucket_name, blob_name = gcs_uri[5:].split("/", 1)
    # blob_name pattern: sage50/raw/YYYY/MM/DD/{report_type}/filename
    parts = blob_name.split("/")
    try:
        export_date = datetime(int(parts[2]), int(parts[3]), int(parts[4]), tzinfo=timezone.utc)
    except (IndexError, ValueError):
        export_date = datetime.now(timezone.utc)

    date_prefix = export_date.strftime("%Y/%m/%d")
    filename = parts[-1]
    staging_name = f"sage50/staging/{date_prefix}/{report_type.value}/{filename}"

    client = gcs_lib.Client()
    bucket = client.bucket(bucket_name)
    bucket.copy_blob(bucket.blob(blob_name), bucket, staging_name)
    return f"gs://{bucket_name}/{staging_name}"
