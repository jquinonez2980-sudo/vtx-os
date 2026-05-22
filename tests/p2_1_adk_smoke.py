"""
tests/p2_1_adk_smoke.py
P2.1 smoke test — ADK SupervisorAgent + OrchestratorAgent integration.

LIVE TEST: makes a real Gemini API call via Vertex AI (ADC required).
BQ writes use a MockBQClient so no data is written to production.

Expected behaviour:
  1. run_sync() sends a GL reconciliation request in natural language
  2. SupervisorAgent (Gemini) calls dispatch_task("RECONCILE_GL", "{...}")
  3. dispatch_task calls OrchestratorAgent → ReconcileGLAgent
  4. ReconcileGLAgent produces MATCHED=19, UNMATCHED_BANK=1, UNMATCHED_GL=2
  5. Supervisor summarises the result in natural language
  6. MockBQClient shows 22 reconciliation rows written

Prerequisites:
  gcloud auth application-default login
  config/project.env contains GOOGLE_GENAI_USE_VERTEXAI, GOOGLE_CLOUD_PROJECT,
  GOOGLE_CLOUD_LOCATION
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# MockBQClient — same pattern as P2.2 test
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
# Smoke test
# ---------------------------------------------------------------------------

REQUEST = (
    "Reconcile the GL for Concetta Enterprises account xxxx5443 for period 2025-12. "
    "Use GL CSV path data/test-client/concetta-dec2025-gl.csv "
    "and bank CSV path data/test-client/dec-2025-bank-extracted.csv. "
    "GL bank account is 1060."
)


def run() -> None:
    mock = MockBQClient()
    _inject(mock)

    # Import runner AFTER injection so the BQ singleton is already replaced
    from agents.adk_runner import run_sync

    print("=" * 60)
    print("P2.1 ADK Smoke Test — SupervisorAgent + ReconcileGLAgent")
    print("=" * 60)
    print(f"\nSending request to SupervisorAgent (live Vertex AI call)...")
    print(f"  {REQUEST}\n")

    response = run_sync(REQUEST)

    print("--- SupervisorAgent response ---")
    print(response)
    print()

    checks: list[tuple[str, bool]] = []

    # ---- ADK returned a response ----
    checks.append(("Supervisor returned non-empty response", bool(response and response.strip())))

    # ---- BQ writes confirm dispatch_task was called and agent ran ----
    recon_rows = sum(len(v) for k, v in mock.inserted.items() if "gl_reconciliation" in k)
    audit_rows = sum(len(v) for k, v in mock.inserted.items() if "audit_log" in k)

    checks.append(("22 reconciliation rows written to BQ", recon_rows == 22))
    checks.append(("Audit events written",                  audit_rows >= 4))

    # ---- Spot-check BQ results ----
    all_recon = [r for k, v in mock.inserted.items()
                 if "gl_reconciliation" in k for r in v]

    matched       = [r for r in all_recon if r.get("match_status") == "MATCHED"]
    unmatched_bnk = [r for r in all_recon if r.get("match_status") == "UNMATCHED_BANK"]
    unmatched_gl  = [r for r in all_recon if r.get("match_status") == "UNMATCHED_GL"]

    checks.append(("19 MATCHED rows",        len(matched)       == 19))
    checks.append(("1 UNMATCHED_BANK row",   len(unmatched_bnk) == 1))
    checks.append(("2 UNMATCHED_GL rows",    len(unmatched_gl)  == 2))

    # ---- Key figures in supervisor response text (natural language) ----
    resp_lower = response.lower()
    checks.append(("Response mentions reconcili",    "reconcil" in resp_lower))
    checks.append(("Response mentions matched",      "match" in resp_lower))
    checks.append(("Response mentions unmatched",    "unmatched" in resp_lower or "not reconciled" in resp_lower))

    # ---- Print results ----
    print("--- Checks ---")
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
