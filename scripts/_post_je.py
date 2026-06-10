"""
scripts/_post_je.py  (manual posting helper)
Post journal entries into Sage 50 from the VERIFIED BigQuery categorized data
(not by re-parsing CSVs), so Sage matches exactly what was reviewed in BQ.

NOTE: this is the raw manual tool — it posts EVERYTHING in
bank_transactions_categorized for the account (needs_review rows go to
suspense) and ignores dashboard approve/reject decisions. For approval-aware
posting use scripts/posting_agent.py (dashboard "Post to Sage 50" flow).

Each bank transaction becomes one balanced BNK journal entry:
    deposit  (amount > 0): Dr Bank / Cr <gl>
    payment  (amount < 0): Dr <gl> / Cr Bank
GL display codes map to Sage lIds inside ledger/sage50.py (code * 10000).

    # dry-run (reads BQ only, no Sage access):
    python scripts/_post_je.py --account xxxx1555 --gl-bank 1060 --suspense 5800
    # real post (Sage 50 must be CLOSED):
    VTX_SAGE50_PASSWORD=... python scripts/_post_je.py --account xxxx1555 \
        --gl-bank 1060 --suspense 5800 --sai "R:\\...\\2025.SAI" --user sysadmin --commit
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

PROJECT = "vtx-accounting-os-prod"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, help="masked account_no in BQ, e.g. xxxx1555")
    ap.add_argument("--gl-bank", required=True, help="bank GL display code, e.g. 1060")
    ap.add_argument("--suspense", default="5800", help="suspense GL for needs_review rows")
    ap.add_argument("--sai", default=None)
    ap.add_argument("--user", default="sysadmin")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--retry-failed", default=None,
                    help="path to a prior run log; re-post ONLY the entries that "
                         "FAILED there (by their deterministic position), so the "
                         "already-posted entries are never duplicated")
    ap.add_argument("--from-date", default=None,
                    help="only fetch/post entries on or after this date (YYYY-MM-DD). "
                         "Use to re-post a date range without duplicating earlier entries.")
    ap.add_argument("--no-dedupe", action="store_true",
                    help="skip the Sage-side duplicate check before posting "
                         "(default: entries already in Sage GL are skipped)")
    ap.add_argument("--no-backup", action="store_true",
                    help="skip the pre-post .SAI/.SAJ backup (default: backup before --commit)")
    args = ap.parse_args()

    from google.cloud import bigquery

    from ledger import build_bank_entries
    from ledger.sage50 import Sage50Connector, lid

    c = bigquery.Client(project=PROJECT)
    date_clause = ""
    bq_params = [bigquery.ScalarQueryParameter("a", "STRING", args.account)]
    if args.from_date:
        date_clause = " AND txn_date >= @from_date"
        bq_params.append(bigquery.ScalarQueryParameter("from_date", "DATE", args.from_date))
    rows = list(c.query(
        "SELECT txn_date, description, amount, gl_account_no, needs_review "
        "FROM vtx_accounting.bank_transactions_categorized "
        f"WHERE account_no=@a{date_clause} ORDER BY txn_date, description",
        job_config=bigquery.QueryJobConfig(query_parameters=bq_params)
    ).result())
    print(f"BQ categorized rows for {args.account}: {len(rows)}")
    if not rows:
        return 1

    # needs_review rows post to suspense (this tool ignores dashboard decisions)
    build_rows = [{
        "txn_date": r.txn_date,
        "description": r.description,
        "amount": r.amount,
        "gl": args.suspense if r.needs_review else (r.gl_account_no or args.suspense),
        "queue_id": None,
    } for r in rows]
    entries = build_bank_entries(build_rows, bank_ref=args.gl_bank,
                                 suspense_ref=args.suspense)

    per_month: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # n, suspense
    gl_tally: dict[str, int] = defaultdict(int)
    for r, e in zip([r for r in rows if r.amount != 0], entries):
        mk = e.entry_date.isoformat()[:7]
        per_month[mk][0] += 1
        if r.needs_review:
            per_month[mk][1] += 1
        gl = next((l.gl_ref for l in e.lines if l.gl_ref != args.gl_bank), args.gl_bank)
        gl_tally[gl] += 1

    skipped_zero = len(rows) - len(entries)
    print(f"\nBuilt {len(entries)} balanced entries"
          + (f"  ({skipped_zero} zero-amount rows skipped)" if skipped_zero else ""))
    print(f"{'month':<9} {'entries':>7} {'suspense':>9}")
    for mk in sorted(per_month):
        n, susp = per_month[mk]
        print(f"{mk:<9} {n:>7} {susp:>9}")
    print("\nentries by GL credit/debit target:")
    for gl in sorted(gl_tally, key=lambda k: -gl_tally[k]):
        print(f"  {gl} -> lId {lid(gl)}: {gl_tally[gl]}")

    # Retry mode: keep only the entries that FAILED in a prior run, identified by
    # their 1-based position in the bridge results (order is deterministic given
    # the same query). This never re-posts the entries that already succeeded.
    if args.retry_failed:
        import re as _re
        log = Path(args.retry_failed).read_text(encoding="utf-8", errors="replace")
        failed_idx = sorted(int(m) for m in
                            _re.findall(r"\[\s*(\d+)\]\s+FAIL", log))
        if not failed_idx:
            print("No FAIL entries found in the log — nothing to retry.")
            return 0
        if max(failed_idx) > len(entries):
            print(f"ERROR: log references entry #{max(failed_idx)} but only "
                  f"{len(entries)} built — data changed since the run. Aborting.")
            return 1
        entries = [entries[i - 1] for i in failed_idx]
        print(f"\n[retry] re-posting ONLY the {len(entries)} previously-failed "
              f"entries (positions {failed_idx[0]}..{failed_idx[-1]})")

    unbalanced = [e for e in entries if not e.is_balanced()]
    print(f"\nbalanced: {len(entries) - len(unbalanced)}/{len(entries)}")

    if not args.commit:
        print("\n[dry-run] no Sage write. Sample entries:")
        for e in entries[:3] + entries[-2:]:
            l0, l1 = e.lines
            print(f"  {e.entry_date}  Dr {lid(l0.gl_ref)} {l0.debit:.2f} | "
                  f"Cr {lid(l1.gl_ref)} {l1.credit:.2f}  {e.comment[:39]}")
        print("\nRe-run with --commit (and Sage 50 CLOSED) to post.")
        return 0

    if not args.sai:
        print("ERROR: --sai is required for --commit")
        return 1

    conn = Sage50Connector(args.sai, user=args.user)
    conn.validate()

    if not args.no_backup:
        print(f"[backup] -> {conn.backup()}")

    if not args.no_dedupe:
        d_lo = min(e.entry_date for e in entries)
        d_hi = max(e.entry_date for e in entries)
        try:
            existing = conn.existing_keys(d_lo, d_hi)
        except Exception as exc:
            print(f"ERROR: Sage duplicate check failed ({exc}).\n"
                  f"Refusing to post blind — re-run with --no-dedupe to override.")
            return 1
        before = len(entries)
        entries = [e for e in entries if conn.key(e) not in existing]
        skipped = before - len(entries)
        if skipped:
            for_msg = "entry" if skipped == 1 else "entries"
            print(f"[dedupe] skipped {skipped} {for_msg} already posted; "
                  f"{len(entries)} remain")
        if not entries:
            print("[dedupe] nothing left to post — all entries already in Sage.")
            return 0

    print(f"\n[commit] posting {len(entries)} entries to {args.sai} ...")
    res = conn.post(entries)
    print(f"posted={res.posted} total={len(entries)} errors={res.errors}")
    for i, r in enumerate(res.results):
        if not r["posted"]:
            e = entries[i]
            print(f"  FAIL {e.entry_date} {e.comment[:30]} : {(r['error'] or '')[:60]}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
