"""
tests/p2_3_hst_return.py
Offline E2E test for P2.3 — HST Return Preparation Agent.

Uses MockBQClient (no real GCP) + local CSV file for tax summary data.

Expected results for Concetta Enterprises Inc. Dec 2025:
  Tax code:           H (Ontario HST 13%)
  Line 101 revenue:   $45,000.00
  Line 103 collected: $5,850.00
  Line 106 ITCs:      $261.21
  Line 109 net tax:   $5,588.79
  is_refund:          False
  filing_due_date:    2026-01-31
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# MockBQClient — same pattern as p2_2_reconcile_gl.py
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
        self.inserted.setdefault(table_id, []).extend(rows)
        return []

    def query(self, sql, **_):
        job = MagicMock()
        job.result.return_value = []
        return job


def _inject(client):
    import core.bq_loader, core.audit
    core.bq_loader._client = client
    core.audit._client     = client


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

TAX_CSV       = "data/test-client/concetta-dec2025-tax.csv"
RETURN_PERIOD = "2025-12"
BUSINESS_NO   = "123456789RT0001"


def run() -> None:
    mock = MockBQClient()
    _inject(mock)

    from agents.orchestrator import OrchestratorAgent
    from agents.base import TaskRequest, TaskType

    req = TaskRequest(
        task_type=TaskType.PREPARE_HST_RETURN,
        payload={
            "tax_csv_path":  TAX_CSV,
            "return_period": RETURN_PERIOD,
            "business_no":   BUSINESS_NO,
        },
    )

    result = OrchestratorAgent().run(req)

    checks: list[tuple[str, bool]] = []

    # ---- Pipeline status ----
    checks.append(("Agent returned SUCCESS", result.status.value == "SUCCESS"))
    checks.append(("No error message",       result.error is None))

    out = result.output or {}

    # ---- Return period / dates ----
    checks.append(("return_period = 2025-12",     out.get("return_period") == "2025-12"))
    checks.append(("period_start = 2025-12-01",   out.get("period_start") == "2025-12-01"))
    checks.append(("period_end = 2025-12-31",     out.get("period_end") == "2025-12-31"))
    checks.append(("filing_due_date = 2026-01-31", out.get("filing_due_date") == "2026-01-31"))

    # ---- Business info ----
    checks.append(("province = ON",              out.get("province") == "ON"))
    checks.append(("business_no passed through", out.get("business_no") == BUSINESS_NO))

    # ---- GST34 lines ----
    line_101 = Decimal(str(out.get("line_101_total_revenue", "0")))
    line_103 = Decimal(str(out.get("line_103_hst_collected", "0")))
    line_106 = Decimal(str(out.get("line_106_itc_claimed",   "0")))
    line_109 = Decimal(str(out.get("line_109_net_tax",       "0")))

    checks.append(("Line 101 = 45000.00", line_101 == Decimal("45000.00")))
    checks.append(("Line 103 = 5850.00",  line_103 == Decimal("5850.00")))
    checks.append(("Line 106 = 261.21",   line_106 == Decimal("261.21")))
    checks.append(("Line 109 = 5588.79",  line_109 == Decimal("5588.79")))
    checks.append(("is_refund = False",   out.get("is_refund") is False))

    # ---- Line counts ----
    checks.append(("1 tax code line",              out.get("line_count") == 1))
    checks.append(("tax_codes_applied = ['H']",    out.get("tax_codes_applied") == ["H"]))

    # ---- BQ writes ----
    hst_rows   = sum(len(v) for k, v in mock.inserted.items() if "hst_returns" in k)
    audit_rows = sum(len(v) for k, v in mock.inserted.items() if "audit_log" in k)

    checks.append(("1 HST return row written to BQ", hst_rows == 1))
    checks.append(("Audit events written",            audit_rows >= 4))

    # ---- Spot-check BQ row ----
    all_hst_rows = [r for k, v in mock.inserted.items()
                    if "hst_returns" in k for r in v]

    checks.append(("BQ row has tax_code H",
                   all_hst_rows and all_hst_rows[0].get("tax_code") == "H"))
    checks.append(("BQ row line_net_tax = 5588.79",
                   all_hst_rows and Decimal(str(all_hst_rows[0].get("line_net_tax", "0"))) == Decimal("5588.79")))

    # ---- Print results ----
    print("=" * 60)
    print("P2.3 HST Return Preparation Agent — Offline Test")
    print(f"Client: Concetta Enterprises Inc. | {RETURN_PERIOD}")
    print("=" * 60)

    print(f"\n--- HSTReturnSummary ---")
    for k, v in out.items():
        if k != "bq_lines_table":
            print(f"  {k:<28} {v}")

    print("\n--- GST34 Lines ---")
    print(f"  Line 101 (Total Revenue):   ${line_101:>10,.2f}")
    print(f"  Line 103 (HST Collected):   ${line_103:>10,.2f}")
    print(f"  Line 106 (ITCs Claimed):    ${line_106:>10,.2f}")
    print(f"  Line 109 (Net Tax Owing):   ${line_109:>10,.2f}")

    print("\n--- Checks ---")
    passed = 0
    for desc, ok in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {desc}")
        if ok:
            passed += 1

    total = len(checks)
    print(f"\n{passed}/{total} checks passed")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    run()
