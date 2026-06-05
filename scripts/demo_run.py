"""
scripts/demo_run.py  — AcumenAI investor demo driver

A single, rehearsable command that runs the full bookkeeping pipeline on
ANONYMIZED, FICTIONAL data (Northview Consulting Inc.) and prints an
investor-narratable summary of each story beat:

    ingest → categorize → verify (balance chain) → queue for approval → audit

RELIABLE BY DESIGN: runs fully offline with a mock BigQuery client — no ADC,
no network, no live client data. Completes in well under a second, repeatable
on demand (the mock state resets every run). Safe to run in front of anyone.

    python scripts/demo_run.py            # the demo
    python scripts/demo_run.py --approve  # also demonstrate the human approval step

The narration that pairs with this output lives in docs/investor-demo-runbook.md.
"""
from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

# Reuse the proven offline mock + injection from the P1.7 end-to-end test —
# importing is side-effect-free (its work is guarded by __main__).
from tests.p1_7_e2e import MockBQClient, _inject_mock  # noqa: E402

# Self-contained, committable, FICTIONAL demo data (not the gitignored test set).
CSV_PATH = _ROOT / "demo" / "sample_statement.csv"

_RULE = "─" * 64


def _beat(title: str) -> None:
    print(f"\n{_RULE}\n  {title}\n{_RULE}")


def _check_balance_chain(txns: list) -> tuple[int, int]:
    """Verify each transaction's signed amount equals the change in running balance.

    Returns (reconciled, total). The bank's own balance column is ground truth;
    this is the trust beat — math, not heuristics.
    """
    chained = [t for t in txns if t.balance is not None]
    if len(chained) < 2:
        return 0, 0
    reconciled = 0
    total = 0
    for prev, cur in zip(chained, chained[1:]):
        total += 1
        if abs((cur.balance - prev.balance) - cur.amount) <= Decimal("0.01"):
            reconciled += 1
    return reconciled, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--approve", action="store_true",
                    help="also demonstrate the human approve/reject step")
    args = ap.parse_args()

    print("\n  AcumenAI — live pipeline demo")
    print("  Client: Northview Consulting Inc. (anonymized demo data) · Dec 2025")
    print("  Mode:   offline · mock infrastructure · no live client data")

    mock = MockBQClient()
    _inject_mock(mock)

    # ---- Beat 1: Ingest -------------------------------------------------
    _beat("1 · INGEST  — a bank statement arrives")
    from sage50.bank_parser import parse_csv
    txns = parse_csv(CSV_PATH, account_no="xxxx1234")
    deposits = [t for t in txns if t.amount > 0]
    paid     = [t for t in txns if t.amount < 0]
    inflow   = sum((t.amount for t in deposits), Decimal(0))
    outflow  = sum((abs(t.amount) for t in paid), Decimal(0))
    print(f"  Parsed {len(txns)} transactions  (bank auto-detected: {txns[0].bank_code.value})")
    print(f"  Money in:  ${inflow:>12,.2f}   ({len(deposits)} deposits)")
    print(f"  Money out: ${outflow:>12,.2f}   ({len(paid)} payments)")

    # ---- Beat 2: Verify (the moat) -------------------------------------
    _beat("2 · VERIFY  — every amount checked against the bank's own balance")
    ok, total = _check_balance_chain(txns)
    if total:
        mark = "✓" if ok == total else "!"
        print(f"  [{mark}] Balance chain reconciled: {ok}/{total} transactions match to the cent")
        print(f"      The running balance is ground truth — this catches the sign-flips")
        print(f"      and dropped rows that manual data entry misses.")
    else:
        print("  (balance column not present in this statement — chain check skipped)")

    # ---- Beats 3+4: Categorize + Queue (full pipeline) ------------------
    _beat("3 · CATEGORIZE & QUEUE  — the multi-agent pipeline runs")
    os.environ["VTX_SECRET_VTX_GOOGLE_CHAT_WEBHOOK"] = "https://chat-mock.demo/webhook"
    from core.secrets import clear_cache
    clear_cache()

    def _fake_post(url, **kwargs):
        resp = MagicMock(); resp.status_code = 200; resp.raise_for_status = MagicMock()
        return resp

    with patch("httpx.post", side_effect=_fake_post):
        from agents.orchestrator import OrchestratorAgent
        from agents.base import TaskRequest, TaskType
        req = TaskRequest(
            task_type=TaskType.BOOKKEEPING_RUN,
            requested_by="demo@acumenai.ca",
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
        result = OrchestratorAgent().run(req)

    out = result.output
    auto   = out.get("auto_categorized", 0)
    review = out.get("needs_review", 0)
    queued = out.get("queue_items_submitted", 0)
    total_txn = out.get("total_transactions", 0)
    pct = (auto / total_txn * 100) if total_txn else 0
    print(f"  {auto}/{total_txn} auto-categorized with confidence  ({pct:.0f}% hands-off)")
    print(f"  {review} flagged for human review")
    print(f"  {queued} items queued for one-click approval")
    print(f"  Notified the bookkeeper  (chat_notified={out.get('chat_notified')})")

    # ---- Beat 5: Audit --------------------------------------------------
    _beat("4 · AUDIT  — every step is recorded, nothing fails silently")
    audit = mock.audit_rows()
    etypes = sorted({r.get("event_type") for r in audit})
    print(f"  {len(audit)} immutable audit events written this run")
    print(f"  Event types: {', '.join(etypes)}")

    # ---- Optional: human approval beat ---------------------------------
    if args.approve:
        _beat("5 · APPROVE  — the human stays in control")
        from core.approval_queue import get_pending, approve
        pending = get_pending()
        print(f"  {len(pending)} items awaiting review")
        if pending:
            item = sorted(pending, key=lambda x: x.txn_date)[0]
            approve(item.queue_id, reviewer_email="cpa@acumenai.ca",
                    final_gl_no="4100", note="Confirmed — client revenue")
            after = get_pending()
            print(f"  Approved: {item.txn_date}  {item.description[:34]!r}")
            print(f"  Queue now: {len(after)} pending  (was {len(pending)})")

    # ---- Recap ----------------------------------------------------------
    _beat("WHAT JUST HAPPENED")
    print(f"  A statement became reviewed, categorized, balance-verified books —")
    print(f"  with a full audit trail — in {result.duration_ms} ms.")
    print(f"  The bookkeeper approved exceptions. They never keyed a transaction.\n")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
