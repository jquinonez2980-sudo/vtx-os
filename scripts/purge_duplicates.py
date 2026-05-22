"""
scripts/purge_duplicates.py
One-shot cleanup: detect and reverse duplicate journal entries in Sage 50.

Sage 50 does not allow deleting posted journal entries. This script identifies
duplicates by composite key (date, source, total_debit, header_comment) and
posts reversing (equal-and-opposite) entries for each copy beyond the first.
The entry with the lowest journal ID (lJEntID) is kept as the authoritative one.

Usage
-----
    # Preview — shows what will be reversed, makes no changes:
    python scripts/purge_duplicates.py ^
        --sai "R:\\Concetta Enterprises Inc\\2026.SAI" ^
        --user sysadmin ^
        --start-date 2026-01-01 --end-date 2026-01-31

    # Execute — posts reversing entries for all duplicates:
    python scripts/purge_duplicates.py ^
        --sai "R:\\Concetta Enterprises Inc\\2026.SAI" ^
        --user sysadmin ^
        --start-date 2026-01-01 --end-date 2026-01-31 ^
        --execute

    # Confirm cleanup (should report 0 duplicates):
    python scripts/purge_duplicates.py ^
        --sai "R:\\Concetta Enterprises Inc\\2026.SAI" ^
        --user sysadmin ^
        --start-date 2026-01-01 --end-date 2026-01-31

Notes
-----
  - Sage 50 must be CLOSED before running (bridge opens .SAI exclusively).
  - Reversing entries use source "RVRSL" to distinguish them from originals.
  - Each reversing entry comment is "VOID DUP #<journal_no>" (max 39 chars).
  - After execution, re-run in preview mode to confirm 0 duplicates remain.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# GL fetch and grouping
# ---------------------------------------------------------------------------

def _fetch_and_group(
    sai: str,
    user: str,
    password: str,
    start: date,
    end: date,
    source_filter: str,
) -> dict[str, list]:
    """Return { journal_no: [GLTransaction, ...] } for entries matching source_filter in [start, end]."""
    from sage50.bridge_reader import fetch_gl_transactions

    rows = fetch_gl_transactions(
        start_date=start,
        end_date=end,
        sai_file=sai,
        user=user,
        password=password,
    )

    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        if (r.source.upper() != source_filter.upper()
                or r.transaction_date is None
                or not (start <= r.transaction_date <= end)):
            continue
        groups[r.journal_no].append(r)

    return dict(groups)


# ---------------------------------------------------------------------------
# Composite key and duplicate detection
# ---------------------------------------------------------------------------

def _entry_key(lines: list) -> tuple[str, str, str, str]:
    """Composite key: (date_iso, source, total_debit_2dp, description_39).

    total_debit == total_credit for any balanced entry, so it is unambiguous.
    description comes from the GL header comment (hdrComment), which is what
    JournalEntryAgent stores as the transaction description.
    """
    if not lines:
        return ("", "", "", "")
    txn_date    = lines[0].transaction_date
    source      = lines[0].source.upper()
    description = lines[0].description[:39]
    total_debit = sum((r.debit for r in lines), Decimal("0"))
    return (
        txn_date.isoformat() if txn_date else "",
        source,
        f"{total_debit:.2f}",
        description,
    )


def find_duplicates(groups: dict[str, list]) -> list[list[str]]:
    """Return duplicate groups as [[keep_id, dup_id, ...], ...].

    keep_id is the lowest lJEntID (first posted); dup_ids are the extras to reverse.
    """
    by_key: dict[tuple, list[str]] = defaultdict(list)
    for jid, lines in groups.items():
        by_key[_entry_key(lines)].append(jid)

    def _int_key(jid: str) -> int:
        try:
            return int(jid)
        except ValueError:
            return 0

    result = []
    for jids in by_key.values():
        if len(jids) > 1:
            result.append(sorted(jids, key=_int_key))
    return result


# ---------------------------------------------------------------------------
# Reversing entry construction
# ---------------------------------------------------------------------------

def _build_reversal(lines: list, journal_no: str) -> dict:
    """Build an equal-and-opposite journal entry for the given GL lines.

    Debit lines become credit lines and vice versa.
    Source is "RVRSL"; comment is "VOID DUP #<journal_no>".
    """
    txn_date = lines[0].transaction_date
    comment  = f"VOID DUP #{journal_no}"[:39]

    reversal_lines = []
    for line in lines:
        if line.debit > 0:
            reversal_lines.append({
                "account_id": line.account_no,
                "debit":      0.0,
                "credit":     float(line.debit),
                "comment":    comment,
            })
        elif line.credit > 0:
            reversal_lines.append({
                "account_id": line.account_no,
                "debit":      float(line.credit),
                "credit":     0.0,
                "comment":    comment,
            })

    if not reversal_lines:
        raise ValueError(f"Entry #{journal_no} has no non-zero lines — cannot reverse")

    return {
        "date":    txn_date.isoformat() if txn_date else "",
        "source":  "RVRSL",
        "comment": comment,
        "lines":   reversal_lines,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detect and reverse duplicate journal entries in Sage 50",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  Preview:  python scripts/purge_duplicates.py --sai ... --start-date 2026-01-01 --end-date 2026-01-31\n"
            "  Execute:  python scripts/purge_duplicates.py --sai ... --start-date 2026-01-01 --end-date 2026-01-31 --execute"
        ),
    )
    parser.add_argument("--sai",         required=True,  help="Path to Sage 50 .SAI company file")
    parser.add_argument("--user",        default="sysadmin", help="Sage 50 username (default: sysadmin)")
    parser.add_argument("--password",    default="",     help="Sage 50 password (default: empty)")
    parser.add_argument("--start-date",  required=True,  help="Scan start date YYYY-MM-DD")
    parser.add_argument("--end-date",    required=True,  help="Scan end date YYYY-MM-DD")
    parser.add_argument("--source",      default="BNK",  help="Journal source code to scan (default: BNK)")
    parser.add_argument("--execute",     action="store_true",
                        help="Post reversing entries. Omit for preview-only (safe default).")
    args = parser.parse_args()

    start = date.fromisoformat(args.start_date)
    end   = date.fromisoformat(args.end_date)

    print("\nVTX-OS Duplicate Journal Purge")
    print(f"  SAI:        {args.sai}")
    print(f"  Date range: {start}  to  {end}")
    print(f"  Source:     {args.source}")
    print(f"  Mode:       {'EXECUTE — reversing entries WILL be posted' if args.execute else 'PREVIEW — no changes will be made'}")
    print()

    # ── Fetch GL ───────────────────────────────────────────────────────────
    print(f"Fetching {args.source} entries from Sage 50...", flush=True)
    try:
        groups = _fetch_and_group(args.sai, args.user, args.password, start, end, args.source)
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    total_lines   = sum(len(v) for v in groups.values())
    total_entries = len(groups)
    print(f"  {total_lines} GL lines across {total_entries} journal entries\n")

    # ── Detect duplicates ─────────────────────────────────────────────────
    dup_groups = find_duplicates(groups)

    if not dup_groups:
        print("No duplicates found.")
        return

    total_to_reverse = sum(len(g) - 1 for g in dup_groups)
    print(f"Found {len(dup_groups)} duplicate group(s) — {total_to_reverse} entr{'y' if total_to_reverse == 1 else 'ies'} to reverse:\n")

    # ── Build reversals and print preview ─────────────────────────────────
    reversals: list[dict] = []
    build_errors: list[str] = []

    for group in dup_groups:
        keep_id   = group[0]
        dup_ids   = group[1:]
        key       = _entry_key(groups[keep_id])

        print(f"  {key[0]}  source={key[1]}  amount=${key[2]}  comment={key[3]!r}")
        print(f"    KEEP    journal #{keep_id:>6}  ({len(groups[keep_id])} lines)")
        for dup_id in dup_ids:
            lines = groups[dup_id]
            print(f"    REVERSE journal #{dup_id:>6}  ({len(lines)} lines)")
            try:
                reversals.append(_build_reversal(lines, dup_id))
            except Exception as exc:
                msg = f"journal #{dup_id}: {exc}"
                print(f"      WARNING: {msg}")
                build_errors.append(msg)
        print()

    if build_errors:
        print(f"WARNING: {len(build_errors)} reversal(s) could not be built (see above).")

    print(f"{len(reversals)} reversing entr{'y' if len(reversals) == 1 else 'ies'} ready to post.")

    if not args.execute:
        print("\nThis was a preview. Add --execute to post the reversing entries.")
        return

    if not reversals:
        print("\nNothing to post.")
        return

    # ── Post reversals ─────────────────────────────────────────────────────
    print(f"\nPosting {len(reversals)} reversing entr{'y' if len(reversals) == 1 else 'ies'}...", flush=True)
    from sage50.bridge_reader import post_journal_entries
    try:
        result = post_journal_entries(
            reversals,
            sai_file=args.sai,
            user=args.user,
            password=args.password,
        )
    except Exception as exc:
        print(f"\nERROR posting reversals: {exc}", file=sys.stderr)
        sys.exit(1)

    posted = result.get("posted", 0)
    errors = result.get("errors", 0)

    print(f"\nResult: {posted} posted, {errors} error(s)")

    if errors:
        print("\nFailed entries:")
        for r in result.get("results", []):
            if not r.get("posted"):
                print(f"  {r.get('date', '?')}  {r.get('comment', '?')}  error={r.get('error', '?')}")
        sys.exit(1)

    print("\nDone. Re-run in preview mode to confirm 0 duplicates remain:")
    print(
        f"  python scripts/purge_duplicates.py "
        f"--sai \"{args.sai}\" --user {args.user} "
        f"--start-date {start} --end-date {end}"
    )


if __name__ == "__main__":
    main()
