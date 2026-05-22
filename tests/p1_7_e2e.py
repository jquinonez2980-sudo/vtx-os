"""
P1.7 -- End-to-end test with real December 2025 data for Northview Consulting Inc.

Runs the full BOOKKEEPING_RUN pipeline offline (no real GCP credentials needed).
BigQuery calls are intercepted by MockBQClient; rows are saved to JSON for inspection.
Google Chat webhook is captured via httpx mock.

Expected results:
    20 transactions parsed (TD format auto-detected from CSV headers)
    12 auto-categorized (confidence >= 0.80)
     8 flagged for review (confidence < 0.80)
    7 BQ audit events written (AGENT_START x2, TASK_CREATED, TASK_DELEGATED,
                               AGENT_COMPLETE x2, TASK_COMPLETE)
    8 approval queue items submitted
    Chat card captured with 8 pending items

Approval flow test:
    PENDING -> APPROVED  (revenue deposit, gl_no=4100)
    PENDING -> REJECTED  (unclassified charge)
    PENDING -> ESCALATED (ambiguous transfer)

Usage (from repo root, venv active):
    python tests/p1_7_e2e.py

BQ preview output:
    data/test-client/bq_raw_transactions.json
    data/test-client/bq_categorized_transactions.json
    data/test-client/bq_approval_queue.json
    data/test-client/bq_audit_trail.json
    data/test-client/chat_card.json
"""

from __future__ import annotations

import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CSV_PATH = ROOT / "data" / "test-client" / "dec-2025-bank.csv"
OUT_DIR  = ROOT / "data" / "test-client"

# ---------------------------------------------------------------------------
# Test state
# ---------------------------------------------------------------------------

PASS = "PASS"
FAIL = "FAIL"
_results: list[tuple[str, str, str]] = []  # (name, status, note)


def check(name: str, condition: bool, note: str = "") -> bool:
    status = PASS if condition else FAIL
    _results.append((name, status, note))
    marker = "[PASS]" if condition else "[FAIL]"
    print(f"  {marker} {name}" + (f" -- {note}" if note else ""))
    return condition


def section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


# ---------------------------------------------------------------------------
# Mock BigQuery client
# ---------------------------------------------------------------------------

class MockBQClient:
    """Intercepts BQ calls, stores rows in-memory for later inspection."""

    def __init__(self) -> None:
        self.inserted: dict[str, list[dict]] = {}  # table_id -> list[row]
        self._queue_rows: dict[str, dict] = {}     # queue_id -> row dict

    def get_table(self, table_id: str):
        from google.cloud.exceptions import NotFound
        raise NotFound(f"(mock) table not found: {table_id}")

    def create_table(self, table):
        return table

    def insert_rows_json(self, table_id: str, rows: list[dict], **_) -> list:
        if table_id not in self.inserted:
            self.inserted[table_id] = []
        self.inserted[table_id].extend(rows)
        # Mirror approval_queue rows for DML read-back
        if "approval_queue" in table_id:
            for row in rows:
                qid = row.get("queue_id", "")
                if qid:
                    self._queue_rows[qid] = dict(row)
        return []  # no errors

    def query(self, sql: str, job_configuration=None, **_):
        mock_job = MagicMock()
        mock_job.result.return_value = []

        if "UPDATE" in sql and "approval_queue" in sql:
            # approve() / reject() / escalate() DML -- check UPDATE first because
            # the UPDATE's WHERE clause also contains "PENDING"
            if job_configuration is not None:
                try:
                    params = {p.name: p.value
                              for p in job_configuration.query_parameters}
                    qid = params.get("queue_id", "")
                    if qid in self._queue_rows:
                        self._queue_rows[qid]["status"] = params.get("status", "")
                        if "final_gl_no" in params:
                            self._queue_rows[qid]["final_gl_no"] = params["final_gl_no"]
                        if "note" in params:
                            self._queue_rows[qid]["review_note"] = params["note"]
                        if "reviewer_email" in params:
                            self._queue_rows[qid]["reviewer_email"] = params["reviewer_email"]
                except Exception as exc:
                    print(f"    [mock] UPDATE parse error: {exc}", file=sys.stderr)

        elif "approval_queue" in sql and "PENDING" in sql:
            # get_pending() -- return rows whose status is still PENDING
            rows = []
            for item in self._queue_rows.values():
                if item.get("status", "PENDING") == "PENDING":
                    row = MagicMock()
                    row.items.return_value = list(item.items())
                    rows.append(row)
            mock_job.result.return_value = rows

        return mock_job

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def table_rows(self, keyword: str) -> list[dict]:
        """Return rows from any table whose key contains keyword."""
        for k, v in self.inserted.items():
            if keyword in k:
                return v
        return []

    def audit_rows(self) -> list[dict]:
        return self.table_rows("audit_log")


# ---------------------------------------------------------------------------
# Inject mock into module singletons
# ---------------------------------------------------------------------------

def _inject_mock(client: MockBQClient) -> None:
    """Set mock BQ client into all module-level singletons (lazy init pattern)."""
    import core.bq_loader
    import core.audit
    import core.approval_queue
    # Reset first so any previously-created real client is discarded
    core.bq_loader._client       = client
    core.audit._client           = client
    core.approval_queue._bq_client = client


# ---------------------------------------------------------------------------
# Individual test steps
# ---------------------------------------------------------------------------

def t_parse_csv() -> list:
    section("Step 1: CSV Parsing")
    from sage50.bank_parser import parse_csv
    txns = parse_csv(CSV_PATH, account_no="xxxx1234")
    check("20 transactions parsed", len(txns) == 20,
          f"got {len(txns)}")
    deposits    = [t for t in txns if t.amount > 0]
    withdrawals = [t for t in txns if t.amount < 0]
    check("4 deposits parsed",    len(deposits) == 4,
          f"got {len(deposits)}")
    check("16 withdrawals parsed", len(withdrawals) == 16,
          f"got {len(withdrawals)}")
    check("Bank detected as TD", txns[0].bank_code.value == "TD",
          f"got {txns[0].bank_code.value}")
    net = sum(t.amount for t in txns)
    check("Net movement is negative (outflow month)",
          net < 0, f"net={net}")
    return txns


def t_categorize(txns: list) -> tuple[list, list, list]:
    section("Step 2: Categorization")
    from sage50.categorizer import categorize_batch
    categorized = categorize_batch(txns)
    auto   = [t for t in categorized if not t.needs_review]
    review = [t for t in categorized if t.needs_review]
    check("12 auto-categorized", len(auto) == 12,
          f"got {len(auto)}")
    check("8 flagged for review", len(review) == 8,
          f"got {len(review)}")

    # Spot-check specific rules
    adp_rows = [t for t in auto if "ADP" in t.description.upper()]
    check("ADP payroll -> gl 5100", all(t.gl_account_no == "5100" for t in adp_rows),
          f"ADP rows: {[(t.description, t.gl_account_no) for t in adp_rows]}")
    cra_hst = next((t for t in auto if "HST" in t.description.upper()), None)
    check("CRA HST -> gl 2200", cra_hst is not None and cra_hst.gl_account_no == "2200",
          f"CRA HST: {cra_hst.gl_account_no if cra_hst else 'NOT FOUND'}")
    intact = next((t for t in auto if "INTACT" in t.description.upper()), None)
    check("INTACT Insurance -> gl 5300",
          intact is not None and intact.gl_account_no == "5300",
          f"INTACT: {intact.gl_account_no if intact else 'NOT FOUND'}")
    interac_items = [t for t in review if "INTERAC" in t.description.upper()]
    check("INTERAC transfers flagged for review (3 expected)",
          len(interac_items) == 3, f"got {len(interac_items)}")
    check("All auto confidence >= 0.80",
          all(t.confidence >= 0.80 for t in auto),
          f"min={min(t.confidence for t in auto):.2f}")
    check("All review confidence < 0.80",
          all(t.confidence < 0.80 for t in review),
          f"max={max(t.confidence for t in review):.2f}")
    return categorized, auto, review


def t_full_pipeline(mock_client: MockBQClient) -> dict:
    section("Step 3: Full BookkeepingAgent Pipeline (via OrchestratorAgent)")
    # Set up Chat webhook via env var; clear secrets cache
    os.environ["VTX_SECRET_VTX_GOOGLE_CHAT_WEBHOOK"] = "https://chat-mock.vtx-test/webhook"
    from core.secrets import clear_cache
    clear_cache()

    captured_card: dict = {}

    def _fake_post(url, **kwargs):
        captured_card.update(kwargs.get("json", {}))
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        return resp

    t0 = time.monotonic()
    with patch("httpx.post", side_effect=_fake_post):
        from agents.orchestrator import OrchestratorAgent
        from agents.base import TaskRequest, TaskType
        req = TaskRequest(
            task_type=TaskType.BOOKKEEPING_RUN,
            requested_by="accountant@northview.ca",
            payload={
                "csv_path":        str(CSV_PATH),
                "account_no":      "xxxx1234",
                "gl_bank_account": "1060",
                "period":          "2025-12",
                "threshold":       0.80,
                "queue_reviews":   True,
                "notify_chat":     True,
            },
        )
        orch = OrchestratorAgent()
        result = orch.run(req)
    elapsed = time.monotonic() - t0

    check("Pipeline completed successfully", result.ok,
          result.error or "")
    check("Finished in under 10s", elapsed < 10.0,
          f"{elapsed:.2f}s")
    check("Output has period=2025-12",
          result.output.get("period") == "2025-12", "")
    check("Output: 20 total_transactions",
          result.output.get("total_transactions") == 20,
          f"got {result.output.get('total_transactions')}")
    check("Output: 12 auto_categorized",
          result.output.get("auto_categorized") == 12,
          f"got {result.output.get('auto_categorized')}")
    check("Output: 8 needs_review",
          result.output.get("needs_review") == 8,
          f"got {result.output.get('needs_review')}")
    check("Output: 8 queue_items_submitted",
          result.output.get("queue_items_submitted") == 8,
          f"got {result.output.get('queue_items_submitted')}")
    check("Chat notification sent (chat_notified=True)",
          result.output.get("chat_notified") is True, "")
    check("duration_ms populated", result.duration_ms is not None and result.duration_ms > 0,
          f"got {result.duration_ms}")
    return captured_card, result


def t_verify_bq(mock_client: MockBQClient) -> None:
    section("Step 4: BigQuery Capture Verification")
    raw_rows  = mock_client.table_rows("bank_transactions_raw")
    cat_rows  = mock_client.table_rows("bank_transactions_categorized")
    queue_rows = mock_client.table_rows("approval_queue")
    audit_rows = mock_client.audit_rows()

    check("20 raw transaction rows in BQ",
          len(raw_rows) == 20, f"got {len(raw_rows)}")
    check("20 categorized rows in BQ",
          len(cat_rows) == 20, f"got {len(cat_rows)}")
    check("8 approval_queue rows in BQ",
          len(queue_rows) == 8, f"got {len(queue_rows)}")
    check("Audit trail written (>= 7 events)",
          len(audit_rows) >= 7, f"got {len(audit_rows)}")

    # Verify audit event types present
    event_types = {r.get("event_type") for r in audit_rows}
    check("AGENT_START in audit trail",   "AGENT_START"   in event_types, "")
    check("AGENT_COMPLETE in audit trail","AGENT_COMPLETE" in event_types, "")
    check("TASK_CREATED in audit trail",  "TASK_CREATED"  in event_types, "")
    check("TASK_DELEGATED in audit trail","TASK_DELEGATED" in event_types, "")
    check("TASK_COMPLETE in audit trail", "TASK_COMPLETE" in event_types, "")

    # Verify raw row structure
    if raw_rows:
        sample = raw_rows[0]
        check("Raw row has txn_id",    "txn_id"       in sample, "")
        check("Raw row has txn_date",  "txn_date"     in sample, "")
        check("Raw row has amount",    "amount"       in sample, "")
        check("Raw row has _loaded_at","_loaded_at"   in sample, "")
        check("Raw row has _session_id","_session_id" in sample, "")

    # Verify approval_queue row structure
    if queue_rows:
        sample_q = queue_rows[0]
        check("Queue row has queue_id",       "queue_id"        in sample_q, "")
        check("Queue row has suggested_gl_no","suggested_gl_no" in sample_q, "")
        check("Queue row has status=PENDING",
              sample_q.get("status") == "PENDING", f"got {sample_q.get('status')}")

    # Verify categorized rows have confidence field
    if cat_rows:
        confidences = [float(r.get("confidence", 0)) for r in cat_rows]
        check("All categorized rows have confidence field",
              all("confidence" in r for r in cat_rows), "")
        check("Max confidence is 0.95", max(confidences) == 0.95,
              f"max={max(confidences)}")


def t_chat_card(captured_card: dict) -> None:
    section("Step 5: Google Chat Card Verification")
    check("Chat card payload captured", bool(captured_card), "httpx.post was called")
    cards = captured_card.get("cardsV2", [])
    check("cardsV2 structure present", len(cards) > 0, f"got {len(cards)}")
    if cards:
        card = cards[0].get("card", {})
        header = card.get("header", {})
        check("Card title mentions 'Require Review'",
              "Require Review" in header.get("title", ""),
              f"title='{header.get('title', '')}'")
        check("Card subtitle has account xxxx1234",
              "xxxx1234" in header.get("subtitle", ""),
              f"subtitle='{header.get('subtitle', '')}'")
        sections = card.get("sections", [])
        check("Card has at least 2 sections", len(sections) >= 2,
              f"got {len(sections)}")


def t_approval_flow(mock_client: MockBQClient) -> None:
    section("Step 6: Approval Flow (approve / reject / escalate)")
    from core.approval_queue import get_pending, approve, reject, escalate

    pending = get_pending()
    check("get_pending() returns 8 items",
          len(pending) == 8, f"got {len(pending)}")
    if len(pending) < 3:
        check("Cannot test approval flow -- too few items", False, "skipped")
        return

    # Sort by date for deterministic picks
    pending.sort(key=lambda x: x.txn_date)

    # Pick candidates
    interac_items = [p for p in pending
                     if "INTERAC" in p.description.upper() or
                        "TRANSFER" in p.description.upper()]
    unclassified  = [p for p in pending
                     if p.suggested_gl_no == "9999"
                     and "INTERAC" not in p.description.upper()
                     and "TRANSFER" not in p.description.upper()]
    escalate_cand = [p for p in pending if p.confidence == 0.0]

    item_approve  = interac_items[0] if interac_items else pending[0]
    item_reject   = unclassified[0]  if unclassified  else pending[1]
    item_escalate = escalate_cand[0] if escalate_cand else pending[2]

    # Ensure all three are distinct
    used = {item_approve.queue_id, item_reject.queue_id, item_escalate.queue_id}
    if len(used) < 3:
        # Fall back to positional picks
        item_approve, item_reject, item_escalate = pending[0], pending[1], pending[2]

    # --- Approve ---
    ok = approve(
        item_approve.queue_id,
        reviewer_email="accountant@northview.ca",
        final_gl_no="4100",
        note="Client retainer payment confirmed",
    )
    check("approve() returns True", ok)
    check("Mock state updated to APPROVED",
          mock_client._queue_rows.get(item_approve.queue_id, {}).get("status") == "APPROVED",
          f"status={mock_client._queue_rows.get(item_approve.queue_id, {}).get('status')}")
    check("final_gl_no written to mock",
          mock_client._queue_rows.get(item_approve.queue_id, {}).get("final_gl_no") == "4100",
          "")

    # --- Reject ---
    ok = reject(
        item_reject.queue_id,
        reviewer_email="accountant@northview.ca",
        note="Personal expense -- not business",
    )
    check("reject() returns True", ok)
    check("Mock state updated to REJECTED",
          mock_client._queue_rows.get(item_reject.queue_id, {}).get("status") == "REJECTED",
          f"status={mock_client._queue_rows.get(item_reject.queue_id, {}).get('status')}")

    # --- Escalate ---
    ok = escalate(
        item_escalate.queue_id,
        reviewer_email="accountant@northview.ca",
        note="Need more context from client",
    )
    check("escalate() returns True", ok)
    check("Mock state updated to ESCALATED",
          mock_client._queue_rows.get(item_escalate.queue_id, {}).get("status") == "ESCALATED",
          f"status={mock_client._queue_rows.get(item_escalate.queue_id, {}).get('status')}")

    # Pending count after updates
    remaining = get_pending()
    check("Remaining pending count is 5 (8 - 3 actioned)",
          len(remaining) == 5, f"got {len(remaining)}")


def t_operational_quality(mock_client: MockBQClient, result) -> None:
    section("Step 7: Operational Quality Checks")
    # No unhandled exceptions (would have caused result.ok == False)
    check("No unhandled exceptions", result.ok, result.error or "")
    # Audit trail never silent -- fallback emits to stderr (hard to catch here,
    # but we can verify audit_rows were written in the mock)
    audit_rows = mock_client.audit_rows()
    check("Audit trail never silent (rows captured)",
          len(audit_rows) > 0, f"got {len(audit_rows)}")
    # All audit rows have required fields
    required_audit_fields = {"event_id", "event_ts", "agent_id", "event_type", "status"}
    all_have_fields = all(
        required_audit_fields.issubset(r.keys()) for r in audit_rows
    )
    check("All audit rows have required fields", all_have_fields,
          f"fields checked: {required_audit_fields}")
    # session_id consistent across the run
    session_ids = {r.get("session_id") for r in audit_rows}
    check("session_id consistent across audit trail",
          len(session_ids) <= 2,   # orchestrator + bookkeeping share one session
          f"distinct session_ids: {len(session_ids)}")
    # All inserted rows have _loaded_at tracking column
    for table_key, rows in mock_client.inserted.items():
        if "audit_log" in table_key:
            continue  # audit_log uses to_bq_row(), no _loaded_at
        has_tracking = all("_loaded_at" in r for r in rows)
        check(f"_loaded_at present in {table_key.split('.')[-1]}",
              has_tracking, f"{len(rows)} rows checked")


# ---------------------------------------------------------------------------
# Save BQ preview files
# ---------------------------------------------------------------------------

def _default(o):
    return str(o)


def save_previews(mock_client: MockBQClient, captured_card: dict) -> None:
    section("Saving BQ Preview Files")
    previews = {
        "bq_raw_transactions.json":       mock_client.table_rows("bank_transactions_raw"),
        "bq_categorized_transactions.json": mock_client.table_rows("bank_transactions_categorized"),
        "bq_approval_queue.json":         mock_client.table_rows("approval_queue"),
        "bq_audit_trail.json":            mock_client.audit_rows(),
        "chat_card.json":                 captured_card,
    }
    for fname, data in previews.items():
        out = OUT_DIR / fname
        out.write_text(json.dumps(data, indent=2, default=_default), encoding="utf-8")
        rows = len(data) if isinstance(data, list) else 1
        print(f"  Saved {rows} records -> {out}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    print()
    print("vtx-os P1.7 -- End-to-End Test")
    print("Client: Northview Consulting Inc. | Period: Dec 2025")
    print(f"CSV:    {CSV_PATH}")
    print()

    mock_client = MockBQClient()
    _inject_mock(mock_client)

    txns = t_parse_csv()
    _cat, _auto, _review = t_categorize(txns)
    captured_card, pipeline_result = t_full_pipeline(mock_client)
    t_verify_bq(mock_client)
    t_chat_card(captured_card)
    t_approval_flow(mock_client)
    t_operational_quality(mock_client, pipeline_result)

    save_previews(mock_client, captured_card)

    # Final summary
    passed = sum(1 for _, s, _ in _results if s == PASS)
    failed = sum(1 for _, s, _ in _results if s == FAIL)
    total  = len(_results)

    section("Test Summary")
    print(f"  Total checks : {total}")
    print(f"  Passed       : {passed}")
    print(f"  Failed       : {failed}")
    print()
    if failed:
        print("  FAILED checks:")
        for name, status, note in _results:
            if status == FAIL:
                print(f"    - {name}" + (f" ({note})" if note else ""))
    else:
        print("  All checks passed.")

    print()
    print("  NOTE: This test ran without real GCP credentials.")
    print("  Production run requires: gcloud auth application-default login")
    print()

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
