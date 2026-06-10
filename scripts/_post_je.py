"""
scripts/_post_je.py  (one-off helper)
Post journal entries into Sage 50 from the VERIFIED BigQuery categorized data
(not by re-parsing CSVs), so Sage matches exactly what was reviewed in BQ.

Each bank transaction becomes one balanced BNK journal entry:
    deposit  (amount > 0): Dr Bank / Cr <gl>
    payment  (amount < 0): Dr <gl> / Cr Bank
needs_review rows post to the suspense account (reclassify in Sage later).
GL display codes map to Sage lIds as code * 10000 (verified for this company).

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
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

PROJECT = "vtx-accounting-os-prod"


def _lid(code: str) -> str:
    """Sage 50 display code -> 8-digit lId (e.g. '1060' -> '10600000')."""
    return str(int(code) * 10000)


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

    bank_lid = _lid(args.gl_bank)
    entries = []
    per_month: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])  # n, suspense, gl-count
    gl_tally: dict[str, int] = defaultdict(int)
    bad = 0
    for r in rows:
        amt = Decimal(str(r.amount))
        if amt == 0:
            bad += 1
            continue
        gl = args.suspense if r.needs_review else (r.gl_account_no or args.suspense)
        gl_lid = _lid(gl)
        absamt = abs(amt)
        desc = (r.description or "")[:39]
        if amt > 0:               # deposit: Dr Bank / Cr gl
            dr, cr = bank_lid, gl_lid
        else:                     # payment: Dr gl / Cr Bank
            dr, cr = gl_lid, bank_lid
        entries.append({
            "date": r.txn_date.isoformat(),
            "source": "BNK",
            "comment": desc,
            "lines": [
                {"account_id": dr, "debit": float(absamt), "credit": 0.0, "comment": desc},
                {"account_id": cr, "debit": 0.0, "credit": float(absamt), "comment": desc},
            ],
        })
        mk = r.txn_date.isoformat()[:7]
        per_month[mk][0] += 1
        if r.needs_review:
            per_month[mk][1] += 1
        gl_tally[gl] += 1

    print(f"\nBuilt {len(entries)} balanced entries"
          + (f"  ({bad} zero-amount rows skipped)" if bad else ""))
    print(f"{'month':<9} {'entries':>7} {'suspense':>9}")
    for mk in sorted(per_month):
        n, susp, _ = per_month[mk]
        print(f"{mk:<9} {n:>7} {susp:>9}")
    print("\nentries by GL credit/debit target:")
    for gl in sorted(gl_tally, key=lambda k: -gl_tally[k]):
        print(f"  {gl} -> lId {_lid(gl)}: {gl_tally[gl]}")

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

    # sanity: every entry balances by construction
    unbalanced = [e for e in entries
                  if abs(sum(l["debit"] for l in e["lines"])
                         - sum(l["credit"] for l in e["lines"])) > 1e-6]
    print(f"\nbalanced: {len(entries) - len(unbalanced)}/{len(entries)}")

    if not args.commit:
        print("\n[dry-run] no Sage write. Sample entries:")
        for e in entries[:3] + entries[-2:]:
            l0, l1 = e["lines"]
            print(f"  {e['date']}  Dr {l0['account_id']} {l0['debit']:.2f} | "
                  f"Cr {l1['account_id']} {l1['credit']:.2f}  {e['comment']}")
        print("\nRe-run with --commit (and Sage 50 CLOSED) to post.")
        return 0

    if not args.sai:
        print("ERROR: --sai is required for --commit")
        return 1

    # ── Pre-post backup: copy the .SAI file + companion .SAJ folder ─────────
    # One bad batch into a live company file has no undo; this is the undo.
    if not args.no_backup:
        import shutil
        from datetime import datetime
        sai_path = Path(args.sai)
        saj_path = sai_path.with_suffix(".SAJ")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bdir = sai_path.parent / "vtx_backup" / f"{sai_path.stem}_{stamp}"
        bdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sai_path, bdir / sai_path.name)
        if saj_path.is_dir():
            shutil.copytree(saj_path, bdir / saj_path.name)
        print(f"[backup] {sai_path.name}"
              + (f" + {saj_path.name}/" if saj_path.is_dir() else "")
              + f" -> {bdir}")

    # ── Sage-side duplicate check (same key as JournalEntryAgent) ───────────
    # Key: (entry_date_iso, description_39chars, abs_amount_2dp). Reads existing
    # BNK lines from the GL over the batch's date range and skips matches, so
    # re-running this script can never double-post.
    if not args.no_dedupe:
        from datetime import date as _date
        from sage50.bridge_reader import fetch_gl_transactions
        d_lo = min(_date.fromisoformat(e["date"]) for e in entries)
        d_hi = max(_date.fromisoformat(e["date"]) for e in entries)
        try:
            existing_rows = fetch_gl_transactions(
                start_date=d_lo, end_date=d_hi,
                sai_file=args.sai, user=args.user,
            )
        except Exception as exc:
            print(f"ERROR: Sage duplicate check failed ({exc}).\n"
                  f"Refusing to post blind — re-run with --no-dedupe to override.")
            return 1
        existing_keys = {
            (r.transaction_date.isoformat(), r.description[:39],
             f"{max(r.debit, r.credit):.2f}")
            for r in existing_rows
            if r.source.upper() == "BNK" and r.transaction_date is not None
        }
        kept, skipped = [], 0
        for e in entries:
            key = (e["date"], e["comment"], f"{e['lines'][0]['debit']:.2f}")
            if key in existing_keys:
                skipped += 1
                print(f"  [dedupe] SKIP already in Sage: {e['date']}  {e['comment']}")
            else:
                kept.append(e)
        if skipped:
            print(f"[dedupe] skipped {skipped} entr{'y' if skipped == 1 else 'ies'} "
                  f"already posted; {len(kept)} remain")
        entries = kept
        if not entries:
            print("[dedupe] nothing left to post — all entries already in Sage.")
            return 0

    print(f"\n[commit] posting {len(entries)} entries to {args.sai} ...")
    from sage50.bridge_reader import post_journal_entries
    res = post_journal_entries(entries, sai_file=args.sai, user=args.user)
    print(f"posted={res.get('posted')} total={res.get('total')} errors={res.get('errors')}")
    fails = [r for r in res.get("results", []) if not r.get("posted")]
    for f in fails[:15]:
        print(f"  FAIL {f.get('date')} {f.get('comment','')[:30]} : {f.get('error','')[:60]}")
    return 0 if res.get("errors", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
