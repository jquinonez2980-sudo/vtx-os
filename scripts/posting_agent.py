"""
scripts/posting_agent.py
Local Sage 50 posting agent — polls vtx_accounting.post_requests for QUEUED
jobs enqueued by the AcumenAI dashboard and executes them via Sage50Bridge.exe.

Must run on the bookkeeping machine where Sage50Bridge.exe and the Sage 50
.SAI company file are accessible.  Sage 50 itself must be CLOSED; the bridge
opens the .SAI file exclusively.

Usage:
    python scripts/posting_agent.py --once           # claim all queued, then exit
    python scripts/posting_agent.py --watch          # poll every 60 s (default)
    python scripts/posting_agent.py --interval 120   # custom poll interval
    python scripts/posting_agent.py --dry-run --once # print what would be posted, no bridge call
    python scripts/posting_agent.py --account-no xxxx5911 --period 2022-01 --once

Auth: ADC must be configured (gcloud auth application-default login).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import os
os.environ.setdefault("PYTHONUTF8", "1")

from google.cloud import bigquery

from core.bq_loader import PROJECT
from core.post_queue import claim, complete
from models.posting import PostStatus

_ACC = f"{PROJECT}.vtx_accounting"
_DEFAULT_INTERVAL = 60   # seconds


# ---------------------------------------------------------------------------
# BQ helpers
# ---------------------------------------------------------------------------

_bq_client: bigquery.Client | None = None


def _bq() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=PROJECT)
    return _bq_client


def _fetch_queued(account_no: str | None = None, limit: int = 10) -> list[dict]:
    """Return up to *limit* QUEUED post_requests, oldest first."""
    where = "status = 'QUEUED'"
    params: list[bigquery.ScalarQueryParameter] = []
    if account_no:
        where += " AND account_no = @account_no"
        params.append(bigquery.ScalarQueryParameter("account_no", "STRING", account_no))

    sql = f"""
        SELECT request_id, account_no, client_id, period, requested_by, created_at
        FROM `{_ACC}.post_requests`
        WHERE {where}
        ORDER BY created_at ASC
        LIMIT {limit}
    """
    cfg = bigquery.QueryJobConfig(query_parameters=params)
    try:
        rows = list(_bq().query(sql, job_config=cfg).result())
    except Exception as exc:
        # Table doesn't exist yet or BQ unreachable
        print(f"[posting-agent] BQ fetch_queued error: {exc}", file=sys.stderr)
        return []
    return [dict(r.items()) for r in rows]


def _fetch_approved(account_no: str, period: str) -> list[dict]:
    """Return all APPROVED approval_queue items for account_no + period."""
    sql = f"""
        SELECT queue_id, txn_date, description, amount,
               suggested_gl_no, final_gl_no, bank_code, account_no
        FROM `{_ACC}.approval_queue`
        WHERE account_no = @account_no
          AND period     = @period
          AND status     = 'APPROVED'
        ORDER BY txn_date ASC, created_at ASC
    """
    params = [
        bigquery.ScalarQueryParameter("account_no", "STRING", account_no),
        bigquery.ScalarQueryParameter("period",     "STRING", period),
    ]
    cfg = bigquery.QueryJobConfig(query_parameters=params)
    rows = list(_bq().query(sql, job_config=cfg).result())
    return [dict(r.items()) for r in rows]


def _mark_posted(queue_ids: list[str]) -> None:
    """Bulk-update approval_queue rows to POSTED."""
    if not queue_ids:
        return
    ids_lit = ", ".join(f"'{qid}'" for qid in queue_ids)
    sql = f"""
        UPDATE `{_ACC}.approval_queue`
        SET status = 'POSTED', review_note = CONCAT(IFNULL(review_note, ''), ' [posted]')
        WHERE queue_id IN ({ids_lit})
          AND status = 'APPROVED'
    """
    _bq().query(sql).result()


def _gl_bank_account(account_no: str, client_id: str) -> str:
    """Look up gl_bank_account from the client registry; fall back to '1060'."""
    try:
        from core.client_registry import load_registry
        registry = load_registry()
        # registry is keyed by raw account_no (digits); account_no here may be
        # masked (xxxx5911).  Try exact match first, then suffix match.
        digits_only = "".join(c for c in account_no if c.isdigit())
        for cfg in registry.values():
            if cfg.account_no == digits_only:
                return cfg.gl_bank_account or "1060"
            if cfg.account_no.endswith(digits_only[-4:]):
                return cfg.gl_bank_account or "1060"
    except Exception as exc:
        print(
            f"[posting-agent] WARNING: client registry lookup failed ({exc}); "
            "defaulting gl_bank_account to 1060",
            file=sys.stderr,
        )
    return "1060"


# ---------------------------------------------------------------------------
# Entry builder
# ---------------------------------------------------------------------------

def _build_entries(approved_rows: list[dict], bank_account: str) -> list[dict]:
    """Convert APPROVED approval_queue rows to bridge wire-format entries."""
    entries = []
    for row in approved_rows:
        # Use reviewer override if set, else suggested
        gl = str(row.get("final_gl_no") or row.get("suggested_gl_no") or "9999").strip()
        if not gl:
            gl = "9999"

        raw_amount = row.get("amount") or Decimal("0")
        amount = Decimal(str(raw_amount))
        abs_amt = abs(amount)
        if abs_amt == 0:
            continue

        desc = str(row.get("description") or "")[:39]

        if amount > 0:         # deposit  → Dr Bank / Cr Revenue
            debit_acct  = bank_account
            credit_acct = gl
        else:                  # withdrawal → Dr Expense / Cr Bank
            debit_acct  = gl
            credit_acct = bank_account

        txn_date = row.get("txn_date")
        if isinstance(txn_date, date):
            date_str = txn_date.isoformat()
        else:
            date_str = str(txn_date)

        entries.append({
            "date":    date_str,
            "source":  "BNK",
            "comment": desc,
            "lines": [
                {"account_id": debit_acct,  "debit": float(abs_amt), "credit": 0.0, "comment": desc},
                {"account_id": credit_acct, "debit": 0.0,            "credit": float(abs_amt), "comment": desc},
            ],
        })
    return entries


# ---------------------------------------------------------------------------
# Process one PostRequest
# ---------------------------------------------------------------------------

def process_one(req: dict, *, dry_run: bool = False) -> None:
    request_id = req["request_id"]
    account_no = req.get("account_no", "")
    client_id  = req.get("client_id", "")
    period     = req.get("period", "")

    print(
        f"\n[posting-agent] Processing request_id={request_id[:8]}... "
        f"account={account_no}  period={period or '(all)'}",
        file=sys.stderr,
    )

    # Claim it atomically — skip rows still in BQ streaming buffer
    if not dry_run:
        if not claim(request_id):
            print(
                f"[posting-agent] Skipping {request_id[:8]}: still in streaming buffer",
                file=sys.stderr,
            )
            return

    # Pull APPROVED items
    approved = _fetch_approved(account_no, period)
    if not approved:
        msg = f"No APPROVED items for account_no={account_no} period={period}"
        print(f"[posting-agent] {msg}", file=sys.stderr)
        if not dry_run:
            complete(request_id, posted_count=0, error_detail=msg)
        return

    print(f"[posting-agent] {len(approved)} APPROVED items to post", file=sys.stderr)

    # Resolve bank GL account
    bank_gl = _gl_bank_account(account_no, client_id)
    print(f"[posting-agent] Bank GL account: {bank_gl}", file=sys.stderr)

    # Build bridge entries
    entries = _build_entries(approved, bank_gl)
    if not entries:
        msg = "All approved items had zero amount — nothing to post"
        print(f"[posting-agent] {msg}", file=sys.stderr)
        if not dry_run:
            complete(request_id, posted_count=0, error_detail=msg)
        return

    if dry_run:
        print(
            f"[posting-agent] DRY-RUN: would post {len(entries)} entries "
            f"via Sage50Bridge (bank_gl={bank_gl})",
            file=sys.stderr,
        )
        for i, e in enumerate(entries, 1):
            dr = e["lines"][0]
            cr = e["lines"][1]
            print(
                f"  [{i:>3}] {e['date']}  Dr {dr['account_id']} ${dr['debit']:.2f}"
                f"  Cr {cr['account_id']}  | {e['comment'][:35]}",
                file=sys.stderr,
            )
        return

    # Sanity check: warn if Sage 50 appears open (heuristic — no lock file check)
    print(
        "[posting-agent] NOTE: ensure Sage 50 is CLOSED before posting. "
        "The bridge opens the .SAI exclusively.",
        file=sys.stderr,
    )

    try:
        from sage50.bridge_reader import post_journal_entries
        result = post_journal_entries(entries)
    except Exception as exc:
        err = str(exc)
        print(f"[posting-agent] FAILED: {err}", file=sys.stderr)
        complete(request_id, posted_count=0, error_detail=err[:500])
        return

    posted = result.get("posted", 0)
    errors = result.get("errors", 0)
    print(
        f"[posting-agent] Bridge result: posted={posted}  errors={errors}",
        file=sys.stderr,
    )

    # Mark approval_queue rows as POSTED
    posted_ids = [
        row["queue_id"]
        for row, entry_result in zip(approved, result.get("results", []))
        if entry_result.get("posted")
    ]
    # If bridge didn't return per-row results, mark all on full success
    if not posted_ids and errors == 0 and posted > 0:
        posted_ids = [row["queue_id"] for row in approved[:posted]]

    if posted_ids:
        try:
            _mark_posted(posted_ids)
            print(
                f"[posting-agent] Marked {len(posted_ids)} approval_queue rows as POSTED",
                file=sys.stderr,
            )
        except Exception as exc:
            print(f"[posting-agent] WARNING: _mark_posted failed: {exc}", file=sys.stderr)

    error_detail = ""
    if errors > 0:
        failed = [r for r in result.get("results", []) if not r.get("posted")]
        error_detail = f"{errors} error(s): " + "; ".join(
            f"{r.get('date')} {r.get('error', '')}" for r in failed[:5]
        )

    complete(request_id, posted_count=posted, error_detail=error_detail[:500])
    print(
        f"[posting-agent] Done  posted={posted}  errors={errors}",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    account_no_filter = args.account_no or None
    period_filter     = args.period or None
    dry_run           = args.dry_run
    interval          = args.interval

    print(
        f"[posting-agent] Starting  dry_run={dry_run}  "
        f"account_filter={account_no_filter}  period_filter={period_filter}",
        file=sys.stderr,
    )

    while True:
        queued = _fetch_queued(account_no=account_no_filter)

        # Apply period filter (BQ table doesn't have period in WHERE yet — filter in Python)
        if period_filter:
            queued = [q for q in queued if q.get("period") == period_filter]

        if queued:
            print(
                f"[posting-agent] {len(queued)} QUEUED request(s) found",
                file=sys.stderr,
            )
            for req in queued:
                try:
                    process_one(req, dry_run=dry_run)
                except Exception as exc:
                    print(
                        f"[posting-agent] UNHANDLED ERROR for {req.get('request_id','?')}: {exc}",
                        file=sys.stderr,
                    )
        else:
            print(
                f"[posting-agent] No queued requests.  "
                f"{'Exiting.' if args.once else f'Sleeping {interval}s...'}",
                file=sys.stderr,
            )

        if args.once:
            break
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Local Sage 50 posting agent — executes dashboard-enqueued jobs."
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="Poll continuously (default: exit after one pass)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Process all currently QUEUED jobs then exit (default)",
    )
    parser.add_argument(
        "--interval", type=int, default=_DEFAULT_INTERVAL,
        help=f"Poll interval in seconds for --watch (default: {_DEFAULT_INTERVAL})",
    )
    parser.add_argument("--account-no", default="", help="Filter by masked account number")
    parser.add_argument("--period",     default="", help="Filter by period YYYY-MM")
    parser.add_argument("--dry-run",    action="store_true", help="Build entries but skip bridge write")
    args = parser.parse_args()

    # Default is --once unless --watch is explicitly set
    if not args.watch:
        args.once = True

    run(args)


if __name__ == "__main__":
    main()
