"""
scripts/_fix_gl_bank.py  (one-off correction helper)
Fix a batch posted with the WRONG bank GL: post reversing entries against the
wrong GL, then re-post the same transactions against the correct GL.

Context (2026-06): xxxx4733 (Theotherapy BMO) was posted with --gl-bank 1060,
but 1060 is the OTHER Theotherapy account's bank GL — it should have been 1065.
395 entries posted (2025 dates only; the 23 Jan-2026 entries failed on the
fiscal year boundary and need no reversal).

Phase 1: for each original entry, post the mirror image (Dr/Cr swapped,
         comment prefixed "REV:") — nets the wrong-GL postings to zero.
Phase 2: re-post the originals with the correct bank GL.
If any Phase 1 reversal fails, Phase 2 is NOT attempted.

    # dry-run (reads BQ only, no Sage access):
    python scripts/_fix_gl_bank.py --account xxxx4733 --wrong-gl 1060 --correct-gl 1065 \
        --to-date 2025-12-31
    # real run (Sage 50 must be CLOSED):
    python scripts/_fix_gl_bank.py --account xxxx4733 --wrong-gl 1060 --correct-gl 1065 \
        --to-date 2025-12-31 --sai "R:\\Canadian Federation of theotherapy\\2025.SAI" --commit
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

PROJECT = "vtx-accounting-os-prod"


def _lid(code: str) -> str:
    """Sage 50 display code -> 8-digit lId (e.g. '1060' -> '10600000')."""
    return str(int(code) * 10000)


def _backup_sai(sai: str) -> None:
    import shutil
    from datetime import datetime
    sai_path = Path(sai)
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, help="masked account_no in BQ, e.g. xxxx4733")
    ap.add_argument("--wrong-gl", required=True, help="bank GL the batch was wrongly posted to, e.g. 1060")
    ap.add_argument("--correct-gl", required=True, help="bank GL it should have been, e.g. 1065")
    ap.add_argument("--suspense", default="5800", help="suspense GL used in the original run")
    ap.add_argument("--from-date", default=None, help="only entries on/after this date (YYYY-MM-DD)")
    ap.add_argument("--to-date", default=None,
                    help="only entries on/before this date (YYYY-MM-DD). Use 2025-12-31 to "
                         "match the 395 that actually posted (Jan-2026 ones failed).")
    ap.add_argument("--sai", default=None)
    ap.add_argument("--user", default="sysadmin")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    ap.add_argument("--skip-reversal", action="store_true",
                    help="resume mode: reversals already posted, only run Phase 2")
    args = ap.parse_args()

    # ── Rebuild the EXACT entries the original run posted (same query+logic) ──
    from google.cloud import bigquery
    c = bigquery.Client(project=PROJECT)
    where = "account_no=@a"
    bq_params = [bigquery.ScalarQueryParameter("a", "STRING", args.account)]
    if args.from_date:
        where += " AND txn_date >= @from_date"
        bq_params.append(bigquery.ScalarQueryParameter("from_date", "DATE", args.from_date))
    if args.to_date:
        where += " AND txn_date <= @to_date"
        bq_params.append(bigquery.ScalarQueryParameter("to_date", "DATE", args.to_date))
    rows = list(c.query(
        "SELECT txn_date, description, amount, gl_account_no, needs_review "
        "FROM vtx_accounting.bank_transactions_categorized "
        f"WHERE {where} ORDER BY txn_date, description",
        job_config=bigquery.QueryJobConfig(query_parameters=bq_params)
    ).result())
    print(f"BQ categorized rows for {args.account}"
          + (f" (to {args.to_date})" if args.to_date else "") + f": {len(rows)}")
    if not rows:
        return 1

    wrong_lid   = _lid(args.wrong_gl)
    correct_lid = _lid(args.correct_gl)
    reversals, corrected = [], []
    for r in rows:
        amt = Decimal(str(r.amount))
        if amt == 0:
            continue
        gl = args.suspense if r.needs_review else (r.gl_account_no or args.suspense)
        gl_lid = _lid(gl)
        absamt = float(abs(amt))
        desc = (r.description or "")[:39]
        if amt > 0:               # deposit: original was Dr wrong-bank / Cr gl
            orig_dr, orig_cr = wrong_lid, gl_lid
            corr_dr, corr_cr = correct_lid, gl_lid
        else:                     # payment: original was Dr gl / Cr wrong-bank
            orig_dr, orig_cr = gl_lid, wrong_lid
            corr_dr, corr_cr = gl_lid, correct_lid
        rev_desc = ("REV:" + desc)[:39]
        # Reversal = original with Dr/Cr swapped
        reversals.append({
            "date": r.txn_date.isoformat(), "source": "BNK", "comment": rev_desc,
            "lines": [
                {"account_id": orig_cr, "debit": absamt, "credit": 0.0, "comment": rev_desc},
                {"account_id": orig_dr, "debit": 0.0, "credit": absamt, "comment": rev_desc},
            ],
        })
        corrected.append({
            "date": r.txn_date.isoformat(), "source": "BNK", "comment": desc,
            "lines": [
                {"account_id": corr_dr, "debit": absamt, "credit": 0.0, "comment": desc},
                {"account_id": corr_cr, "debit": 0.0, "credit": absamt, "comment": desc},
            ],
        })

    print(f"\nBuilt {len(reversals)} reversal + {len(corrected)} corrected entries")
    print(f"  wrong bank GL : {args.wrong_gl} -> lId {wrong_lid}  (will net to zero)")
    print(f"  correct bank GL: {args.correct_gl} -> lId {correct_lid}  (gets clean detail)")

    if not args.commit:
        print("\n[dry-run] no Sage write. Samples:")
        for label, batch in (("REVERSAL", reversals[:2]), ("CORRECTED", corrected[:2])):
            for e in batch:
                l0, l1 = e["lines"]
                print(f"  {label:<9} {e['date']}  Dr {l0['account_id']} {l0['debit']:.2f} | "
                      f"Cr {l1['account_id']} {l1['credit']:.2f}  {e['comment']}")
        print("\nRe-run with --commit --sai \"...\" (and Sage 50 CLOSED) to post.")
        return 0

    if not args.sai:
        print("ERROR: --sai is required for --commit")
        return 1
    if not args.no_backup:
        _backup_sai(args.sai)

    from sage50.bridge_reader import post_journal_entries

    if not args.skip_reversal:
        print(f"\n[phase 1] posting {len(reversals)} reversals to {args.sai} ...")
        res1 = post_journal_entries(reversals, sai_file=args.sai, user=args.user)
        print(f"reversals: posted={res1.get('posted')} errors={res1.get('errors')}")
        if res1.get("errors", 0) > 0:
            print("ERROR: reversal phase had failures — NOT proceeding to phase 2.\n"
                  "Fix the failures, then resume with --skip-reversal "
                  "(reversals that DID post must not be posted twice).")
            return 1
    else:
        print("\n[phase 1] SKIPPED (--skip-reversal)")

    print(f"\n[phase 2] posting {len(corrected)} corrected entries to {args.sai} ...")
    res2 = post_journal_entries(corrected, sai_file=args.sai, user=args.user)
    print(f"corrected: posted={res2.get('posted')} errors={res2.get('errors')}")
    fails = [r for r in res2.get("results", []) if not r.get("posted")]
    for f in fails[:15]:
        print(f"  FAIL {f.get('date')} {f.get('comment','')[:30]} : {f.get('error','')[:60]}")
    return 0 if res2.get("errors", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
