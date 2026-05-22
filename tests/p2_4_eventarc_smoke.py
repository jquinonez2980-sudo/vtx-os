"""
tests/p2_4_eventarc_smoke.py
P2.4 smoke test - Eventarc GCS trigger + Sage50IngestAgent gcs_uri path.

OFFLINE: no live GCP, no Eventarc, no real GCS calls.
         MockBQClient + patched GCS + mocked functions_framework.

Checks:
   --- Routing logic (_route) - pure function, no GCP calls ---
   1    sage50/raw CSV with known report_type  ->  INGEST_SAGE50_CSV
   2    _route result payload has gcs_uri key
   3    _route result payload has correct report_type
   4    sage50/raw CSV with unknown report_type  ->  None (no route)
   5    sage50/staging path  ->  None (ignored, our own move)
   6    sage50/archive path  ->  None (ignored, our own move)
   7    sage50/failed path   ->  None (ignored, our own move)
   8    odbc-triggers with known type  ->  INGEST_SAGE50_ODBC
   9    odbc-triggers payload has correct report_type
  10    odbc-triggers with unknown type  ->  None (no route)
  11    bank-statements CSV  ->  BOOKKEEPING_RUN
  12    bank-statements payload csv_path == gcs_uri
  13    unmatched path  ->  None (no route)

   --- handle_gcs_finalize() - mocked OrchestratorAgent ---
  14    sage50/raw event dispatches to orchestrator with INGEST_SAGE50_CSV
  15    TaskRequest session_id matches CloudEvent id
  16    TaskRequest requested_by == "eventarc-gcs-trigger"
  17    bank-statements event dispatches BOOKKEEPING_RUN
  18    odbc-triggers event dispatches INGEST_SAGE50_ODBC
  19    unrouted object path -> orchestrator NOT called
  20    missing bucket in event data -> no crash, orchestrator NOT called
  21    task failure (result.ok=False) -> RuntimeError raised by handler

   --- Sage50IngestAgent gcs_uri path - mocked GCS ---
  22    result.ok is True when gcs_uri payload given
  23    output raw_gcs_uri == input gcs_uri (file already in GCS)
  24    output row_count correct (from mocked GCS download content)
  25    output staging_gcs_uri present (to_staging default True)
  26    BQ audit events written during ingest (>= 2)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ---------------------------------------------------------------------------
# Mock functions_framework + cloudevents BEFORE importing the trigger module
# ---------------------------------------------------------------------------
_mock_ff = MagicMock()
_mock_ff.cloud_event = lambda f: f          # decorator is a no-op in tests
sys.modules.setdefault("functions_framework", _mock_ff)

_mock_ce_http = MagicMock()
sys.modules.setdefault("cloudevents.http", _mock_ce_http)
sys.modules.setdefault("cloudevents", MagicMock())


# ---------------------------------------------------------------------------
# MockBQClient
# ---------------------------------------------------------------------------

class MockBQClient:
    def __init__(self):
        self.inserted: dict[str, list[dict]] = {}

    def get_table(self, table_id):
        from google.cloud.exceptions import NotFound
        raise NotFound(f"(mock) {table_id}")

    def create_table(self, table):
        return table

    def insert_rows_json(self, table_id, rows, **_):
        self.inserted.setdefault(str(table_id), []).extend(rows)
        return []

    def query(self, sql, **_):
        job = MagicMock()
        job.result.return_value = []
        return job

    def total_rows(self) -> int:
        return sum(len(v) for v in self.inserted.values())


def _inject(client):
    import core.bq_loader, core.audit
    core.bq_loader._client = client
    core.audit._client     = client


# ---------------------------------------------------------------------------
# Minimal CloudEvent stand-in
# ---------------------------------------------------------------------------

class FakeCloudEvent:
    def __init__(self, event_id: str, bucket: str, name: str):
        self.data = {"bucket": bucket, "name": name}
        self._id  = event_id

    def get(self, key, default=None):
        return self._id if key == "id" else default


# ---------------------------------------------------------------------------
# GCS mock for Sage50IngestAgent gcs_uri path
# ---------------------------------------------------------------------------

CSV_CONTENT = "date,description,amount\n2026-01-01,Widget sale,100.00\n2026-01-02,Service,200.00\n"


def _make_gcs_mock(csv_text: str = CSV_CONTENT) -> MagicMock:
    """Return a mock GCS Client whose blob.download_to_filename writes csv_text to disk."""
    mock_client = MagicMock()

    def fake_download(filename):
        Path(filename).write_text(csv_text, encoding="utf-8")

    (mock_client.return_value
                .bucket.return_value
                .blob.return_value
                .download_to_filename
                .side_effect) = fake_download

    (mock_client.return_value
                .bucket.return_value
                .copy_blob
                .return_value) = MagicMock()   # staging copy no-op

    return mock_client


# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------

def run() -> None:
    mock_bq = MockBQClient()
    _inject(mock_bq)

    from functions.gcs_ingest_trigger import _route, handle_gcs_finalize
    from agents.base import TaskRequest, TaskType
    from agents.sage50_ingest import Sage50IngestAgent
    from agents.orchestrator import OrchestratorAgent

    checks: list[tuple[str, bool]] = []

    # ------------------------------------------------------------------ #
    # Checks 1-13  Pure _route() logic                                    #
    # ------------------------------------------------------------------ #

    BUCKET = "vtx-accounting-os-prod-vtx-exports"

    # 1-3  sage50/raw CSV with known report_type
    name_gl = "sage50/raw/2026/01/15/gl_transactions/export.csv"
    uri_gl  = f"gs://{BUCKET}/{name_gl}"
    tt, pl  = _route(name_gl, uri_gl)
    checks.append(("sage50/raw CSV -> INGEST_SAGE50_CSV",
                   tt == TaskType.INGEST_SAGE50_CSV))
    checks.append(("_route payload has gcs_uri",
                   pl.get("gcs_uri") == uri_gl))
    checks.append(("_route payload report_type == gl_transactions",
                   pl.get("report_type") == "gl_transactions"))

    # 4  Unknown report_type under sage50/raw/
    name_unk = "sage50/raw/2026/01/15/unknown_report/export.csv"
    tt4, _   = _route(name_unk, f"gs://{BUCKET}/{name_unk}")
    checks.append(("unknown report_type under sage50/raw -> None", tt4 is None))

    # 5-7  Internal housekeeping paths are ignored
    for label, path in [
        ("staging", "sage50/staging/2026/01/15/gl_transactions/f.csv"),
        ("archive", "sage50/archive/2026/01/15/gl_transactions/f.csv"),
        ("failed",  "sage50/failed/2026/01/15/gl_transactions/f.csv"),
    ]:
        tt_skip, _ = _route(path, f"gs://{BUCKET}/{path}")
        checks.append((f"sage50/{label} path -> None (ignored)", tt_skip is None))

    # 8-9  odbc-triggers with known type
    name_odbc = "odbc-triggers/ar_invoices.trigger"
    tt8, pl8  = _route(name_odbc, f"gs://{BUCKET}/{name_odbc}")
    checks.append(("odbc-triggers -> INGEST_SAGE50_ODBC",
                   tt8 == TaskType.INGEST_SAGE50_ODBC))
    checks.append(("odbc-triggers payload has correct report_type",
                   pl8.get("report_type") == "ar_invoices"))

    # 10  odbc-triggers with unknown type
    name_odbc_unk = "odbc-triggers/nonexistent.trigger"
    tt10, _ = _route(name_odbc_unk, f"gs://{BUCKET}/{name_odbc_unk}")
    checks.append(("odbc-triggers unknown type -> None", tt10 is None))

    # 11-12  bank-statements CSV
    name_bank = "bank-statements/2026/01/dec-rbc.csv"
    uri_bank  = f"gs://{BUCKET}/{name_bank}"
    tt11, pl11 = _route(name_bank, uri_bank)
    checks.append(("bank-statements CSV -> BOOKKEEPING_RUN",
                   tt11 == TaskType.BOOKKEEPING_RUN))
    checks.append(("bank-statements payload csv_path == gcs_uri",
                   pl11.get("csv_path") == uri_bank))

    # 13  Completely unmatched path
    tt13, _ = _route("random/unrelated/file.csv", "gs://bucket/random/unrelated/file.csv")
    checks.append(("unmatched path -> None", tt13 is None))

    # ------------------------------------------------------------------ #
    # Checks 14-21  handle_gcs_finalize() with mocked orchestrator        #
    # ------------------------------------------------------------------ #

    captured_requests: list[TaskRequest] = []

    def _mock_run(self, request: TaskRequest):
        from agents.base import TaskResult
        from models.base import EventStatus
        captured_requests.append(request)
        return TaskResult(
            task_id=request.task_id,
            task_type=request.task_type,
            agent_id="orchestrator-agent",
            status=EventStatus.SUCCESS,
            output={"mocked": True},
        )

    with patch.object(OrchestratorAgent, "run", _mock_run):

        # 14-16  sage50/raw CSV event
        captured_requests.clear()
        ev = FakeCloudEvent("evt-abc-123", BUCKET, "sage50/raw/2026/01/15/gl_transactions/q1.csv")
        handle_gcs_finalize(ev)

        checks.append(("sage50/raw event -> orchestrator called once",
                       len(captured_requests) == 1))
        if captured_requests:
            req = captured_requests[0]
            checks.append(("TaskRequest.task_type == INGEST_SAGE50_CSV",
                           req.task_type == TaskType.INGEST_SAGE50_CSV))
            checks.append(("TaskRequest.session_id == CloudEvent id",
                           req.session_id == "evt-abc-123"))
            checks.append(("TaskRequest.requested_by == eventarc-gcs-trigger",
                           req.requested_by == "eventarc-gcs-trigger"))
        else:
            for label in ("task_type", "session_id", "requested_by"):
                checks.append((f"TaskRequest.{label} (skipped - no call)", False))

        # 17  bank-statements event
        captured_requests.clear()
        ev17 = FakeCloudEvent("evt-def-456", BUCKET, "bank-statements/2026/01/dec-td.csv")
        handle_gcs_finalize(ev17)
        checks.append(("bank-statements event -> BOOKKEEPING_RUN dispatched",
                       len(captured_requests) == 1
                       and captured_requests[0].task_type == TaskType.BOOKKEEPING_RUN))

        # 18  odbc-triggers event
        captured_requests.clear()
        ev18 = FakeCloudEvent("evt-ghi-789", BUCKET, "odbc-triggers/payroll.trigger")
        handle_gcs_finalize(ev18)
        checks.append(("odbc-triggers event -> INGEST_SAGE50_ODBC dispatched",
                       len(captured_requests) == 1
                       and captured_requests[0].task_type == TaskType.INGEST_SAGE50_ODBC))

        # 19  Unrouted path -> orchestrator NOT called
        captured_requests.clear()
        ev19 = FakeCloudEvent("evt-jkl-000", BUCKET, "random/unrelated/file.csv")
        handle_gcs_finalize(ev19)
        checks.append(("unrouted path -> orchestrator not called",
                       len(captured_requests) == 0))

        # 20  Missing bucket/name -> no crash, no dispatch
        captured_requests.clear()
        ev20 = FakeCloudEvent("evt-mno-111", "", "")
        try:
            handle_gcs_finalize(ev20)
            no_crash = True
        except Exception:
            no_crash = False
        checks.append(("missing bucket/name -> no crash, no dispatch",
                       no_crash and len(captured_requests) == 0))

    # 21  Task failure -> RuntimeError raised
    def _failing_run(self, request: TaskRequest):
        from agents.base import TaskResult
        from models.base import EventStatus
        return TaskResult(
            task_id=request.task_id,
            task_type=request.task_type,
            agent_id="orchestrator-agent",
            status=EventStatus.FAILURE,
            error="BQ write failed",
        )

    with patch.object(OrchestratorAgent, "run", _failing_run):
        ev21 = FakeCloudEvent("evt-fail", BUCKET, "sage50/raw/2026/01/15/gl_transactions/f.csv")
        try:
            handle_gcs_finalize(ev21)
            raised = False
        except RuntimeError:
            raised = True
    checks.append(("task failure -> RuntimeError raised by handler", raised))

    # ------------------------------------------------------------------ #
    # Checks 22-26  Sage50IngestAgent with gcs_uri payload               #
    # ------------------------------------------------------------------ #

    GCS_URI       = f"gs://{BUCKET}/sage50/raw/2026/01/15/gl_transactions/export.csv"
    EXPECTED_ROWS = CSV_CONTENT.count("\n") - 1  # 2 data rows

    mock_gcs_client = _make_gcs_mock(CSV_CONTENT)
    agent = Sage50IngestAgent()
    req_ingest = TaskRequest(
        task_type=TaskType.INGEST_SAGE50_CSV,
        requested_by="test@vtx-os.local",
        payload={
            "gcs_uri":     GCS_URI,
            "report_type": "gl_transactions",
        },
    )

    # Both helpers import google.cloud.storage locally, patch at the source
    with patch("google.cloud.storage.Client", mock_gcs_client):
        result = agent.run(req_ingest)

    checks.append(("Sage50IngestAgent result.ok is True (gcs_uri path)",
                   result.ok))
    checks.append(("output raw_gcs_uri == input gcs_uri",
                   result.output.get("raw_gcs_uri") == GCS_URI))
    checks.append(("output row_count == 2 (from mocked GCS content)",
                   result.output.get("row_count") == EXPECTED_ROWS))
    checks.append(("output staging_gcs_uri present",
                   "staging_gcs_uri" in result.output))
    checks.append(("BQ audit rows written (>= 2 for agent start + complete)",
                   mock_bq.total_rows() >= 2))

    # ------------------------------------------------------------------ #
    # Report                                                               #
    # ------------------------------------------------------------------ #

    passed = sum(1 for _, ok in checks if ok)
    total  = len(checks)
    print(f"\nP2.4 Eventarc smoke test -- {passed}/{total} checks passed\n")
    for i, (label, ok) in enumerate(checks, 1):
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {i:2d}  {label}")

    if passed < total:
        print(f"\n{total - passed} check(s) FAILED.")
        sys.exit(1)
    else:
        print("\nAll checks passed.")


if __name__ == "__main__":
    run()
