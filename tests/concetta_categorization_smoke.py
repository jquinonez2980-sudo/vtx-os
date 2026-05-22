"""
tests/concetta_categorization_smoke.py

Offline smoke test: before/after comparison of Concetta Enterprises categorization.

Before: default Canadian ruleset (0 Concetta-specific rules → 0 auto-categorized)
After:  ConcettaRuleset wired into BookkeepingAgent via client_id="concetta"

Runs entirely offline — no BQ writes. Uses mock BQ client from p1_7_e2e pattern.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# Inject mock BQ before any agent imports so singletons are replaced
# ---------------------------------------------------------------------------

def _inject_mock():
    import core.bq_loader, core.audit, core.approval_queue
    mock = MagicMock()
    mock.insert_rows_json.return_value = []
    mock.query.return_value = MagicMock(result=lambda: None)
    core.bq_loader._client = mock
    core.audit._client = mock
    core.approval_queue._bq_client = mock


_inject_mock()

from agents.base import TaskRequest, TaskType
from agents.bookkeeping import BookkeepingAgent
from sage50.bank_parser import parse_csv

CSV_PATH   = "data/test-client/dec-2025-bank-extracted.csv"
ACCOUNT_NO = "xxxx5443"
PERIOD     = "2025-12"
GL_BANK    = "1060"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(client_id: str = "") -> dict:
    payload = {
        "csv_path":        CSV_PATH,
        "account_no":      ACCOUNT_NO,
        "gl_bank_account": GL_BANK,
        "period":          PERIOD,
        "queue_reviews":   False,
        "notify_chat":     False,
    }
    if client_id:
        payload["client_id"] = client_id

    result = BookkeepingAgent().run(TaskRequest(
        task_type=TaskType.BOOKKEEPING_RUN,
        payload=payload,
    ))
    assert result.output is not None, f"Agent failed: {result.error}"
    return result.output


def _categorize_direct(client_id: str = "") -> list[dict]:
    """Return per-transaction categorization detail without BQ."""
    from sage50.bank_parser import parse_csv
    from sage50.categorizer import DEFAULT_RULES, categorize_batch
    from agents.bookkeeping import _categorize_concetta

    txns = parse_csv(Path(CSV_PATH), account_no=ACCOUNT_NO)
    if client_id == "concetta":
        cats = _categorize_concetta(txns, threshold=0.80)
    else:
        cats = categorize_batch(txns, rules=DEFAULT_RULES, threshold=0.80)
    return [
        {
            "date":        str(c.txn_date),
            "description": c.description,
            "amount":      c.amount,
            "gl_no":       c.gl_account_no,
            "gl_name":     c.gl_account_name,
            "confidence":  c.confidence,
            "needs_review": c.needs_review,
        }
        for c in cats
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    sep = "=" * 70

    print(sep)
    print("Concetta Enterprises - Categorization Smoke Test")
    print(f"CSV: {CSV_PATH}")
    print(sep)

    # ---- BEFORE (default rules) ----
    before_rows = _categorize_direct(client_id="")
    before_auto = sum(1 for r in before_rows if not r["needs_review"])
    before_rev  = sum(1 for r in before_rows if r["needs_review"])

    # ---- AFTER (ConcettaRuleset) ----
    after_rows = _categorize_direct(client_id="concetta")
    after_auto = sum(1 for r in after_rows if not r["needs_review"])
    after_rev  = sum(1 for r in after_rows if r["needs_review"])

    total = len(before_rows)

    print(f"\n{'TRANSACTION':<38} {'BEFORE':>8}  {'AFTER':>8}  NOTE")
    print("-" * 70)
    for b, a in zip(before_rows, after_rows):
        changed = "AUTO" if b["needs_review"] and not a["needs_review"] else ""
        gl_b = b["gl_no"] or "9999"
        gl_a = a["gl_no"] or "9999"
        desc = a["description"][:36]
        print(f"  {desc:<36}  {gl_b:>8}    {gl_a:>8}  {changed}")

    print("-" * 70)
    print(f"\n  BEFORE  —  auto: {before_auto:>2}/{total}   needs_review: {before_rev:>2}/{total}")
    print(f"  AFTER   —  auto: {after_auto:>2}/{total}   needs_review: {after_rev:>2}/{total}")
    new_auto = after_auto - before_auto
    print(f"\n  Net improvement: +{new_auto} transactions auto-categorized")

    # ---- After-only detail ----
    print(f"\n{'AFTER breakdown by GL':}")
    print("-" * 50)
    from collections import Counter
    gl_counts: Counter = Counter()
    for r in after_rows:
        label = f"GL {r['gl_no']:>4}  {r['gl_name']}"
        gl_counts[label] += 1
    for label, cnt in sorted(gl_counts.items()):
        mark = "  (review)" if "Suspense" in label else ""
        print(f"  {cnt:>2}x  {label}{mark}")

    # ---- Full pipeline run via BookkeepingAgent (validates the wiring) ----
    print(f"\n{sep}")
    print("BookkeepingAgent integration check (mock BQ):")
    out_before = _run(client_id="")
    out_after  = _run(client_id="concetta")

    checks = [
        ("Before: total_transactions == 20",      out_before["total_transactions"] == 20),
        ("Before: auto_categorized == 0",         out_before["auto_categorized"] == 0),
        ("After:  total_transactions == 20",      out_after["total_transactions"] == 20),
        (f"After:  auto_categorized == {after_auto}", out_after["auto_categorized"] == after_auto),
        (f"After:  needs_review == {after_rev}",   out_after["needs_review"] == after_rev),
        ("Net movement preserved (Decimal)",
            Decimal(str(out_before["net_movement"])) == Decimal(str(out_after["net_movement"]))),
    ]

    passed = 0
    for desc, ok in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {desc}")
        if ok:
            passed += 1

    print(f"\n{passed}/{len(checks)} checks passed")
    if passed < len(checks):
        sys.exit(1)

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
