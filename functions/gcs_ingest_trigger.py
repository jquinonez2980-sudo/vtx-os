"""
GCS object.finalize Eventarc handler — routes uploaded files to the orchestrator.

Routing rules (GCS object path → TaskType):
  sage50/raw/YYYY/MM/DD/{report_type}/*.csv  →  INGEST_SAGE50_CSV
  odbc-triggers/{report_type}.trigger        →  INGEST_SAGE50_ODBC
  bank-statements/**/*.csv                   →  BOOKKEEPING_RUN
  sage50/{staging,archive,failed}/...        →  ignored (internal housekeeping moves)
  everything else                            →  ignored (logged, no dispatch)

Failure behaviour:
  A RuntimeError on task failure causes Cloud Run to return HTTP 5xx,
  which Eventarc retries according to its configured retry policy.

Deployment entry point:
  gcloud functions deploy vtx-gcs-ingest --entry-point=handle_gcs_finalize
  (imported into project-root main.py so Cloud Functions can find it)
"""

from __future__ import annotations

import logging

import functions_framework
from cloudevents.http import CloudEvent

from agents.base import TaskType
from sage50.csv_uploader import ReportType

logger = logging.getLogger(__name__)

_SAGE50_REPORT_VALUES: frozenset[str] = frozenset(rt.value for rt in ReportType)

# Paths created by our own GCS housekeeping — ignore finalize events for these
_SKIP_PREFIXES = (
    "sage50/staging/",
    "sage50/archive/",
    "sage50/failed/",
)


# ---------------------------------------------------------------------------
# Cloud Run / Cloud Functions entry point
# ---------------------------------------------------------------------------

@functions_framework.cloud_event
def handle_gcs_finalize(cloud_event: CloudEvent) -> None:
    """Receive a GCS object.finalize CloudEvent and dispatch to the orchestrator."""
    data = cloud_event.data or {}
    bucket: str = data.get("bucket", "")
    name: str = data.get("name", "")

    if not bucket or not name:
        logger.warning("CloudEvent missing bucket/name — skipped. data=%s", data)
        return

    gcs_uri = f"gs://{bucket}/{name}"
    logger.info("GCS finalize: %s", gcs_uri)

    task_type, payload = _route(name, gcs_uri)
    if task_type is None:
        logger.info("No route matched for %s — skipped.", name)
        return

    # Lazy import so tests can mock OrchestratorAgent before this runs
    from agents.base import TaskRequest
    from agents.orchestrator import OrchestratorAgent

    session_id = cloud_event.get("id") or "eventarc-unknown"
    request = TaskRequest(
        task_type=task_type,
        payload=payload,
        session_id=session_id,
        requested_by="eventarc-gcs-trigger",
    )

    orchestrator = OrchestratorAgent()
    result = orchestrator.run(request)

    if not result.ok:
        # Non-2xx → Eventarc retry
        raise RuntimeError(
            f"[vtx-os] {task_type.value} failed for {gcs_uri}: {result.error}"
        )

    logger.info("[vtx-os] %s succeeded for %s", task_type.value, gcs_uri)


# ---------------------------------------------------------------------------
# Pure routing logic (no GCP calls — fully testable offline)
# ---------------------------------------------------------------------------

def _route(name: str, gcs_uri: str) -> tuple[TaskType | None, dict]:
    """Map a GCS object path to a (TaskType, payload) pair, or (None, {}) to skip."""

    # Ignore paths created by our own housekeeping moves
    if any(name.startswith(p) for p in _SKIP_PREFIXES):
        return None, {}

    # sage50/raw/YYYY/MM/DD/{report_type}/filename.csv
    # Parts: [sage50, raw, YYYY, MM, DD, {report_type}, filename]  ← 7 segments
    if name.startswith("sage50/raw/") and name.endswith(".csv"):
        parts = name.split("/")
        if len(parts) >= 7:
            report_type_str = parts[5]
            if report_type_str in _SAGE50_REPORT_VALUES:
                return TaskType.INGEST_SAGE50_CSV, {
                    "gcs_uri": gcs_uri,
                    "report_type": report_type_str,
                }

    # odbc-triggers/{report_type}.trigger → kick off an ODBC pull
    if name.startswith("odbc-triggers/") and name.endswith(".trigger"):
        filename = name.rsplit("/", 1)[-1]
        report_type_str = filename.removesuffix(".trigger")
        if report_type_str in _SAGE50_REPORT_VALUES:
            return TaskType.INGEST_SAGE50_ODBC, {
                "report_type": report_type_str,
            }

    # bank-statements/**/*.csv → bookkeeping pipeline
    if name.startswith("bank-statements/") and name.endswith(".csv"):
        return TaskType.BOOKKEEPING_RUN, {
            "csv_path": gcs_uri,
            "account_no": "xxxx",
        }

    return None, {}
