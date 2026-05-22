"""
tests/p2_2_reconcile_gl.py
Offline E2E test for P2.2 — GL Reconciliation Agent.

Uses MockBQClient (no real GCP) + local CSV files for both bank and GL data.

Expected results for Concetta Enterprises Inc. Dec 2025:
  MATCHED        19  (18 exact + CHQ-00720 with $0.16 amount diff)
  UNMATCHED_BANK  1  (CASH WITHDRAWAL $5,000.00 — not recorded in GL)
  UNMATCHED_GL    2  (CHQ-00782 $250.00 outstanding, SERVICE CHARGE $3.75 OCR loss)
  is_reconciled: False
  net_difference: $4,746.09  (gl_net - bank_net)
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# MockBQClient — same pattern as p1_7_e2e.py
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

GL_CSV   = "data/test-client/concetta-dec2025-gl.csv"
BANK_CSV = "data/test-client/dec-2025-bank-extracted.csv"
ACCOUNT  = "xxxx5443"
PERIOD   = "2025-12"


def run() -> None:
    mock = MockBQClient()
    _inject(mock)

    from agents.orchestrator import OrchestratorAgent
    from agents.base import TaskRequest, TaskType

    req = TaskRequest(
        task_type=TaskType.RECONCILE_GL,
        payload={
            "gl_csv_path":    GL_CSV,
            "bank_csv_path":  BANK_CSV,
            "account_no":     ACCOUNT,
            "period":         PERIOD,
            "gl_bank_account": "1060",
            "amount_tolerance": 1.00,
            "date_tolerance_days": 2,
        },
    )

    result = OrchestratorAgent().run(req)

    checks: list[tuple[str, bool]] = []

    # ---- Pipeline status ----
    checks.append(("Agent returned SUCCESS", result.status.value == "SUCCESS"))
    checks.append(("No error message",       result.error is None))

    out = result.output or {}

    # ---- Transaction counts ----
    checks.append(("20 bank transactions loaded",  out.get("bank_txn_count")  == 20))
    checks.append(("21 GL entries loaded",         out.get("gl_entry_count")  == 21))
    checks.append(("19 matched pairs",             out.get("matched_count")   == 19))
    checks.append(("1 unmatched bank txn",         out.get("unmatched_bank_count") == 1))
    checks.append(("2 unmatched GL entries",       out.get("unmatched_gl_count")   == 2))

    # ---- Reconciliation status ----
    checks.append(("Not reconciled (has unmatched items)", out.get("is_reconciled") is False))

    # ---- Financial figures ----
    bank_net = Decimal(str(out.get("bank_net", "0")))
    gl_net   = Decimal(str(out.get("gl_net",   "0")))
    net_diff = Decimal(str(out.get("net_difference", "0")))

    checks.append(("Bank net = +13429.61",       bank_net == Decimal("13429.61")))
    checks.append(("GL net   = +18175.70",       gl_net   == Decimal("18175.70")))
    checks.append(("net_difference = +4746.09",  net_diff == Decimal("4746.09")))

    bank_deposits    = Decimal(str(out.get("total_bank_deposits",    "0")))
    bank_withdrawals = Decimal(str(out.get("total_bank_withdrawals", "0")))
    gl_debits  = Decimal(str(out.get("total_gl_debits",  "0")))
    gl_credits = Decimal(str(out.get("total_gl_credits", "0")))

    checks.append(("Bank deposits  = 23249.07",  bank_deposits    == Decimal("23249.07")))
    checks.append(("Bank withdrawals = 9819.46", bank_withdrawals == Decimal("9819.46")))
    checks.append(("GL debits  = 23249.07",      gl_debits        == Decimal("23249.07")))
    checks.append(("GL credits = 5073.37",       gl_credits       == Decimal("5073.37")))

    # ---- BQ writes ----
    recon_rows  = sum(len(v) for k, v in mock.inserted.items() if "gl_reconciliation" in k)
    audit_rows  = sum(len(v) for k, v in mock.inserted.items() if "audit_log" in k)

    checks.append(("22 reconciliation rows written to BQ", recon_rows == 22))
    checks.append(("Audit events written",                  audit_rows >= 4))

    # ---- Spot-check individual items ----
    all_recon_rows = [r for k, v in mock.inserted.items()
                      if "gl_reconciliation" in k for r in v]

    cash_unmatched = [r for r in all_recon_rows
                      if r.get("match_status") == "UNMATCHED_BANK"
                      and "CASH" in (r.get("bank_description") or "")]
    checks.append(("CASH WITHDRAWAL flagged UNMATCHED_BANK", len(cash_unmatched) == 1))
    checks.append(("CASH WITHDRAWAL amount = -5000.00",
                   cash_unmatched and Decimal(str(cash_unmatched[0]["bank_amount"])) == Decimal("-5000.00")))

    chq782_gl = [r for r in all_recon_rows
                 if r.get("match_status") == "UNMATCHED_GL"
                 and "00782" in (r.get("gl_source_no") or "")]
    checks.append(("CHQ-00782 flagged UNMATCHED_GL", len(chq782_gl) == 1))
    checks.append(("CHQ-00782 GL amount = -250.00",
                   chq782_gl and Decimal(str(chq782_gl[0]["gl_amount"])) == Decimal("-250.00")))

    svc_gl = [r for r in all_recon_rows
              if r.get("match_status") == "UNMATCHED_GL"
              and "service" in (r.get("gl_description") or "").lower()]
    checks.append(("SERVICE CHARGE flagged UNMATCHED_GL", len(svc_gl) == 1))
    checks.append(("SERVICE CHARGE GL amount = -3.75",
                   svc_gl and Decimal(str(svc_gl[0]["gl_amount"])) == Decimal("-3.75")))

    # CHQ-00720: matched but $0.16 amount diff
    chq720_match = [r for r in all_recon_rows
                    if r.get("match_status") == "MATCHED"
                    and "00720" in (r.get("gl_source_no") or "")]
    checks.append(("CHQ-00720 is MATCHED",       len(chq720_match) == 1))
    checks.append(("CHQ-00720 amount_diff = 0.16",
                   chq720_match and Decimal(str(chq720_match[0]["amount_diff"])) == Decimal("0.16")))

    # SENTRIX credit matched as deposit
    sentrix = [r for r in all_recon_rows
               if r.get("match_status") == "MATCHED"
               and Decimal(str(r.get("bank_amount", "0"))) > 0]
    checks.append(("SENTRIX deposit matched as credit", len(sentrix) == 1))
    checks.append(("SENTRIX bank_amount = +23249.07",
                   sentrix and Decimal(str(sentrix[0]["bank_amount"])) == Decimal("23249.07")))

    # ---- Print results ----
    print("=" * 60)
    print("P2.2 GL Reconciliation Agent — Offline Test")
    print(f"Client: Concetta Enterprises Inc. | {PERIOD}")
    print("=" * 60)

    print(f"\n--- ReconciliationSummary ---")
    for k, v in out.items():
        if k != "bq_results_table":
            print(f"  {k:<28} {v}")

    print("\n--- Reconciling Items ---")
    print(f"  MATCHED:        {out.get('matched_count')}")
    print(f"  UNMATCHED_BANK: {out.get('unmatched_bank_count')}  "
          f"(CASH WITHDRAWAL $5,000.00 — not recorded in GL)")
    print(f"  UNMATCHED_GL:   {out.get('unmatched_gl_count')}  "
          f"(CHQ-00782 $250.00 outstanding; SERVICE CHARGE $3.75 OCR loss)")

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
