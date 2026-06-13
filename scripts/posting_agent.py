"""
scripts/posting_agent.py — local Sage 50 posting agent.

Runs on the Windows bookkeeping machine (where Sage 50 + Sage50Bridge.exe +
the R:\\ company files live). Polls vtx_accounting.post_requests for jobs the
dashboard queued via "Post to Sage", builds APPROVAL-AWARE journal entries,
and posts them through the bridge.

What gets posted for a (account_no, period) request:
  - auto-approved transactions  (categorized.needs_review = FALSE) -> gl_account_no
  - reviewer-approved items     (approval_queue.status = APPROVED) -> final_gl_no
What never posts:
  - REJECTED / ESCALATED / still-PENDING / ARCHIVED queue items
  - anything already in Sage (same dedupe key as _post_je.py / JournalEntryAgent)

Safety, in order, before any Sage write:
  1. .SAI + .SAJ backup to <folder>\\vtx_backup\\<stem>_<timestamp>\\
  2. Sage-side duplicate check (refuses to post blind if the check fails)
  3. Entries grouped by calendar year -> posted to that year's .SAI
     (a missing .SAI fails the request with a clear message, e.g. "create
     2026.SAI via Maintenance -> Start New Year")
After posting: APPROVED queue rows flip to POSTED; request marked DONE/FAILED.

    python scripts\\posting_agent.py --once --dry-run     # inspect, no writes
    python scripts\\posting_agent.py --once               # process queue once
    python scripts\\posting_agent.py --watch              # poll every 120s (Sage must be CLOSED while jobs run)
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

PROJECT = "vtx-accounting-os-prod"

# Posting-priority rank for fan-out dedup: lower = higher priority.
_STATUS_RANK: dict[str, int] = {
    "APPROVED": 0, "POSTED": 1, "PENDING": 2, "ESCALATED": 3, "REJECTED": 4,
}


def _post_decision(needs_review: bool, queue_status: str | None) -> bool:
    """The approval contract, in one place:
    - auto-approved rows (needs_review=False) post unless a reviewer overruled
      them (REJECTED/ESCALATED);
    - needs_review rows post ONLY with an explicit APPROVED decision.
    POSTED rows never re-post."""
    qs = (queue_status or "").upper()
    if qs == "POSTED":
        return False
    if needs_review:
        return qs == "APPROVED"
    return qs not in ("REJECTED", "ESCALATED")


def _fetch_postable(account_no: str, period: str) -> list[dict]:
    """Approval-aware entry rows: auto-approved + reviewer-APPROVED, never
    REJECTED/PENDING/ESCALATED. Reviewer GL corrections (final_gl_no) win."""
    from google.cloud import bigquery
    c = bigquery.Client(project=PROJECT)
    where = "t.account_no = @acct"
    params = [bigquery.ScalarQueryParameter("acct", "STRING", account_no)]
    if period:
        where += " AND FORMAT_DATE('%Y-%m', t.txn_date) = @period"
        params.append(bigquery.ScalarQueryParameter("period", "STRING", period))
    sql = f"""
        SELECT
            t.txn_date, t.description, t.amount,
            q.status AS queue_status, q.queue_id,
            COALESCE(q.final_gl_no, t.gl_account_no) AS gl,
            t.needs_review
        FROM `{PROJECT}.vtx_accounting.bank_transactions_categorized` t
        LEFT JOIN `{PROJECT}.vtx_accounting.approval_queue` q
          ON  q.account_no  = t.account_no
          AND q.txn_date    = t.txn_date
          AND q.description = t.description
          AND q.amount      = t.amount
        WHERE {where}
        ORDER BY t.txn_date, t.description
    """
    rows = list(c.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result())

    # Deduplicate fan-out from the LEFT JOIN: one categorized row can match multiple
    # queue rows (re-submission, duplicate import, same date+desc+amount).
    # Keep the row with the highest posting priority for each (date, desc, amount).
    seen_dk: dict[tuple, dict] = {}
    for r in rows:
        dk = (r.txn_date, r.description, str(r.amount))
        prev = seen_dk.get(dk)
        if prev is None:
            seen_dk[dk] = dict(r)
        else:
            new_rank = _STATUS_RANK.get((r.queue_status or "").upper(), 9)
            old_rank = _STATUS_RANK.get((prev.get("queue_status") or "").upper(), 9)
            if new_rank < old_rank:
                seen_dk[dk] = dict(r)
    rows = list(seen_dk.values())

    postable = []
    held = defaultdict(int)
    for r in rows:
        if not _post_decision(r.needs_review, r.queue_status):
            held[(r.queue_status or "NO_QUEUE_ROW").upper()] += 1
            continue
        postable.append({
            "txn_date": r.txn_date, "description": r.description,
            "amount": Decimal(str(r.amount)), "gl": r.gl, "queue_id": r.queue_id,
        })
    if held:
        print("  held back: " + ", ".join(f"{k}={v}" for k, v in sorted(held.items())))
    return postable


def _mark_posted(queue_ids: list[str]) -> int:
    """APPROVED -> POSTED for the queue rows whose entries Sage accepted."""
    ids = [q for q in queue_ids if q]
    if not ids:
        return 0
    from google.cloud import bigquery
    c = bigquery.Client(project=PROJECT)
    sql = f"""
        UPDATE `{PROJECT}.vtx_accounting.approval_queue`
        SET status = 'POSTED', reviewed_at = CURRENT_TIMESTAMP()
        WHERE queue_id IN UNNEST(@ids) AND status = 'APPROVED'
    """
    job = c.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("ids", "STRING", ids)]))
    job.result()
    return job.num_dml_affected_rows or 0


def _process_request(req, dry_run: bool, sage_user: str) -> tuple[int, int, int, str]:
    """Returns (posted, skipped, errors, note)."""
    from core.client_registry import resolve_client
    from ledger import build_bank_entries, connector_for

    cfg = resolve_client(req.account_no)
    if cfg is None:
        return 0, 0, 1, f"account {req.account_no} not in client registry"

    print(f"  client={cfg.client_id} platform={cfg.platform} "
          f"gl_bank={cfg.gl_bank_account}")

    rows = _fetch_postable(req.account_no, req.period)
    if not rows:
        return 0, 0, 0, "nothing approved to post"
    entries = build_bank_entries(rows, bank_ref=cfg.gl_bank_account)
    print(f"  {len(entries)} approval-cleared entries"
          + (f" for {req.period}" if req.period else ""))

    # Sage company files are per fiscal year — one connector per (client, year).
    by_year: dict[int, list] = defaultdict(list)
    for e in entries:
        by_year[e.entry_date.year].append(e)

    total_posted = total_skipped = total_errors = 0
    notes = []
    for year in sorted(by_year):
        batch = by_year[year]
        conn = connector_for(cfg, year, user=sage_user)
        try:
            conn.validate()
        except Exception as exc:
            notes.append(f"{year}: {exc} — {len(batch)} entries held")
            total_errors += len(batch)
            continue
        print(f"  [{year}] {len(batch)} entries -> {conn.platform}")
        try:
            d_lo = min(e.entry_date for e in batch)
            d_hi = max(e.entry_date for e in batch)
            existing = conn.existing_keys(d_lo, d_hi)
        except Exception as exc:
            notes.append(f"{year}: dedupe check failed ({exc}) — held")
            total_errors += len(batch)
            continue
        before = len(batch)
        batch = [e for e in batch if conn.key(e) not in existing]
        skipped = before - len(batch)
        total_skipped += skipped
        if skipped:
            print(f"  [{year}] {skipped} already in ledger — skipped")

        # Within-batch dedupe: two entries with identical conn.key() would cause
        # the first to post and the second to hit a Sage duplicate error.
        seen_keys: set = set()
        deduped = []
        for e in batch:
            k = conn.key(e)
            if k not in seen_keys:
                seen_keys.add(k)
                deduped.append(e)
            else:
                print(f"  [{year}] within-batch duplicate removed: {k}")
        batch = deduped

        if not batch:
            continue
        if dry_run:
            print(f"  [{year}] DRY-RUN — would post {len(batch)}")
            notes.append(f"{year}: dry-run, {len(batch)} ready")
            continue

        bdir = conn.backup()
        if bdir:
            print(f"  [backup] -> {bdir}")
        res = conn.post(batch)
        total_posted += res.posted
        total_errors += res.errors
        # Flip APPROVED -> POSTED only for entries the ledger confirmed
        ok_ids = [batch[i].queue_id for i, r in enumerate(res.results) if r["posted"]]
        flipped = _mark_posted(ok_ids)
        notes.append(f"{year}: posted={res.posted} errors={res.errors} queue->POSTED={flipped}")

    return total_posted, total_skipped, total_errors, "; ".join(notes) or "ok"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="process the queue once and exit")
    ap.add_argument("--watch", action="store_true", help="poll forever")
    ap.add_argument("--interval", type=int, default=120, help="watch poll seconds")
    ap.add_argument("--dry-run", action="store_true",
                    help="claim nothing; show what each QUEUED request would post")
    ap.add_argument("--user", default="sysadmin", help="Sage 50 user")
    args = ap.parse_args()
    if not (args.once or args.watch):
        ap.error("pass --once or --watch")

    from core.post_queue import claim, complete, fetch_queued
    from models.posting import PostRequestStatus

    while True:
        queued = fetch_queued()
        if queued:
            print(f"[agent] {len(queued)} queued request(s)")
        for req in queued:
            print(f"[agent] request {req.request_id[:8]} acct={req.account_no} "
                  f"period={req.period or 'all'} by={req.requested_by}")
            if args.dry_run:
                _process_request(req, dry_run=True, sage_user=args.user)
                continue
            if not claim(req.request_id):
                print("  (claimed elsewhere — skipping)")
                continue
            try:
                posted, skipped, errors, note = _process_request(
                    req, dry_run=False, sage_user=args.user)
                status = PostRequestStatus.DONE if errors == 0 else PostRequestStatus.FAILED
                complete(req.request_id, status,
                         posted=posted, skipped=skipped, errors=errors, note=note)
                print(f"  -> {status.value}: posted={posted} skipped={skipped} "
                      f"errors={errors}  {note}")
            except Exception as exc:
                complete(req.request_id, PostRequestStatus.FAILED, errors=1,
                         note=f"agent crash: {exc}")
                print(f"  -> FAILED: {exc}")
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
