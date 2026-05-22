"""
tests/concetta_live_pipeline.py

Live pipeline run for Concetta Enterprises Inc.
Uses real BigQuery (ADC) — no mocks.

Steps mirrored from P1.7 but with actual GCP writes:
  1. Parse the PDF-extracted CSV
  2. BOOKKEEPING_RUN via OrchestratorAgent (real BQ)
  3. Print summary + spot-checks
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.orchestrator import OrchestratorAgent
from agents.base import TaskRequest, TaskType

CSV_PATH    = "data/test-client/dec-2025-bank-extracted.csv"
ACCOUNT_NO  = "xxxx5443"
PERIOD      = "2025-12"
GL_BANK     = "1060"

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("Concetta Enterprises Inc. — Live Pipeline Run")
    print("CSV:", CSV_PATH)
    print("=" * 60)

    req = TaskRequest(
        task_type=TaskType.BOOKKEEPING_RUN,
        payload={
            "csv_path":        CSV_PATH,
            "account_no":      ACCOUNT_NO,
            "gl_bank_account": GL_BANK,
            "period":          PERIOD,
            "client_id":       "concetta",
            "queue_reviews":   True,
            "notify_chat":     True,
        },
    )

    result = OrchestratorAgent().run(req)

    print(f"\nStatus : {result.status.value}")
    if result.error:
        print(f"Error  : {result.error}")
        sys.exit(1)

    out = result.output or {}
    print(f"\n--- BookkeepingSummary ---")
    for k, v in out.items():
        print(f"  {k:<28} {v}")

    total = out.get("total_transactions", 0)
    auto  = out.get("auto_categorized", 0)
    rev   = out.get("needs_review", 0)
    net   = Decimal(str(out.get("net_movement", "0")))

    print("\n--- Spot-checks ---")
    checks = [
        ("Total transactions >= 18",             total >= 18),
        ("ConcettaRuleset: auto >= 12",          auto >= 12),
        ("ConcettaRuleset: needs_review <= 8",   rev <= 8),
        ("Queue items submitted == needs_review", out.get("queue_items_submitted", 0) == rev),
        ("Net gain from SENTRIX credit",      net > 0),
        ("Raw BQ table set",                  bool(out.get("bq_raw_table"))),
        ("Categorized BQ table set",          bool(out.get("bq_categorized_table"))),
    ]

    passed = 0
    for desc, ok in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {desc}")
        if ok:
            passed += 1

    print(f"\n{passed}/{len(checks)} spot-checks passed")
    if passed < len(checks):
        sys.exit(1)


if __name__ == "__main__":
    main()
