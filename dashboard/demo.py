"""
dashboard/demo.py — capture the offline bookkeeping pipeline as a JSON payload.

`build_demo_payload()` runs the SAME pipeline as scripts/demo_run.py (the rehearsable
investor demo) on the fictional Northview Consulting statement, but instead of printing
to a terminal it returns a structured, JSON-safe dict describing the five story beats:

    1 ingest      — statement parsed, money in/out, bank auto-detected
    2 verify      — every amount checked against the bank's running balance (the moat)
    3 categorize  — multi-agent pipeline: auto-categorized vs queued for review
    4 audit       — immutable audit events written
    5 approve     — a human approves one exception (optional)

This is the single source of truth for the demo artifact. `scripts/export_demo_json.py`
calls it to bake `demo/demo_run.json`, which the orchelix.com showcase page animates.

SAFETY: like demo_run, this injects a MockBQClient into the core module singletons so
NOTHING touches live BigQuery. Critically, it ALWAYS resets those singletons back to
None in a finally block, so any later live use re-creates the real BigQuery client.
The fictional input is deterministic, so the payload is stable across runs.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parents[1]

# Fictional, committable demo data (NOT the gitignored real client set).
CSV_PATH = _ROOT / "demo" / "sample_statement.csv"

# Demo identity (matches scripts/demo_run.py).
_CLIENT = "Northview Consulting Inc."
_PERIOD = "2025-12"
_ACCOUNT_NO = "xxxx1234"
_GL_BANK = "1060"
_THRESHOLD = 0.80


def _money(value: Decimal) -> str:
    """Two-decimal string — money is never emitted as a float (CRA precision rule)."""
    return f"{value:.2f}"


def _check_balance_chain(txns: list) -> tuple[int, int]:
    """Verify each transaction's signed amount equals the change in the running balance.

    The bank's own balance column is ground truth (gotcha #11). Mirrors the helper in
    scripts/demo_run.py. Returns (reconciled, total) over consecutive balance-bearing rows.
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


def _reset_bq_singletons() -> None:
    """Discard the injected mock so live code re-creates the real BigQuery client."""
    import core.approval_queue
    import core.audit
    import core.bq_loader
    core.bq_loader._client = None
    core.audit._client = None
    core.approval_queue._bq_client = None


def build_demo_payload(approve: bool = True) -> dict[str, Any]:
    """Run the offline pipeline and return a JSON-safe dict of the five beats.

    Args:
        approve: include the human-approval beat (approves one queued item).

    The MockBQClient is injected for the duration and always torn down afterward.
    """
    # Imported lazily so importing this module never constructs a real BQ client.
    from tests.p1_7_e2e import MockBQClient, _inject_mock

    mock = MockBQClient()
    _inject_mock(mock)

    # Force the chat webhook through a mocked secret + httpx so no network is touched.
    os.environ["VTX_SECRET_VTX_GOOGLE_CHAT_WEBHOOK"] = "https://chat-mock.demo/webhook"

    def _fake_post(url, **kwargs):  # noqa: ANN001 - mock signature
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        return resp

    try:
        from core.secrets import clear_cache
        clear_cache()

        # ---- Beat 1: Ingest -------------------------------------------------
        from sage50.bank_parser import parse_csv
        txns = parse_csv(CSV_PATH, account_no=_ACCOUNT_NO)
        deposits = [t for t in txns if t.amount > 0]
        payments = [t for t in txns if t.amount < 0]
        inflow = sum((t.amount for t in deposits), Decimal(0))
        outflow = sum((abs(t.amount) for t in payments), Decimal(0))

        ingest = {
            "transactions": len(txns),
            "bank_code": txns[0].bank_code.value if txns else None,
            "money_in": _money(inflow),
            "money_out": _money(outflow),
            "deposits": len(deposits),
            "payments": len(payments),
        }

        # ---- Beat 2: Verify (the moat) -------------------------------------
        reconciled, chain_total = _check_balance_chain(txns)
        verify = {
            "reconciled": reconciled,
            "total": chain_total,
            "all_reconciled": chain_total > 0 and reconciled == chain_total,
        }

        # ---- Beats 3+4: Categorize + Queue (full multi-agent pipeline) ------
        with patch("httpx.post", side_effect=_fake_post):
            from agents.base import TaskRequest, TaskType
            from agents.orchestrator import OrchestratorAgent
            req = TaskRequest(
                task_type=TaskType.BOOKKEEPING_RUN,
                requested_by="demo@acumenai.ca",
                payload={
                    "csv_path": str(CSV_PATH),
                    "account_no": _ACCOUNT_NO,
                    "gl_bank_account": _GL_BANK,
                    "period": _PERIOD,
                    "threshold": _THRESHOLD,
                    "queue_reviews": True,
                    "notify_chat": True,
                },
            )
            result = OrchestratorAgent().run(req)

        out = result.output or {}
        total_txn = out.get("total_transactions", 0)
        auto = out.get("auto_categorized", 0)
        review = out.get("needs_review", 0)
        categorize = {
            "total": total_txn,
            "auto_categorized": auto,
            "needs_review": review,
            "queued": out.get("queue_items_submitted", 0),
            "auto_pct": round(auto / total_txn * 100) if total_txn else 0,
            "chat_notified": bool(out.get("chat_notified")),
        }

        # ---- Beat 5: Audit --------------------------------------------------
        audit_rows = mock.audit_rows()
        audit = {
            "event_count": len(audit_rows),
            "event_types": sorted({r.get("event_type") for r in audit_rows if r.get("event_type")}),
        }

        # ---- Optional Beat: Approve ----------------------------------------
        approve_beat: dict[str, Any] | None = None
        if approve:
            from core.approval_queue import approve as approve_item
            from core.approval_queue import get_pending
            pending = get_pending()
            before = len(pending)
            approved: dict[str, Any] | None = None
            if pending:
                item = sorted(pending, key=lambda x: x.txn_date)[0]
                approve_item(
                    item.queue_id,
                    reviewer_email="cpa@acumenai.ca",
                    final_gl_no="4100",
                    note="Confirmed — client revenue",
                )
                approved = {
                    "txn_date": str(item.txn_date),
                    "description": item.description,
                    "final_gl_no": "4100",
                }
            after = len(get_pending())
            approve_beat = {
                "pending_before": before,
                "pending_after": after,
                "approved": approved,
            }

        duration_ms = result.duration_ms
        payload: dict[str, Any] = {
            "brand": "AcumenAI by Orchelix",
            "client": _CLIENT,
            "period": _PERIOD,
            "mode": "offline · mock infrastructure · fictional data",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ok": bool(result.ok),
            "beats": {
                "ingest": ingest,
                "verify": verify,
                "categorize": categorize,
                "audit": audit,
            },
            "recap": {
                "duration_ms": duration_ms,
                "headline": (
                    "A bank statement became reviewed, categorized, balance-verified "
                    f"books — with a full audit trail — in {duration_ms} ms."
                ),
            },
        }
        if approve_beat is not None:
            payload["beats"]["approve"] = approve_beat
        return payload
    finally:
        _reset_bq_singletons()
        os.environ.pop("VTX_SECRET_VTX_GOOGLE_CHAT_WEBHOOK", None)
        try:
            from core.secrets import clear_cache
            clear_cache()
        except Exception:
            pass
