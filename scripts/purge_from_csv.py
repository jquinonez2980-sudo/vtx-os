"""
scripts/purge_from_csv.py
Purge duplicate BNK journal entries identified from a Sage 50 General Journal
CSV export (Reports → General Journal → export).

Sage 50 cannot delete posted entries; this script posts equal-and-opposite
reversing entries (source RVRSL, comment "VOID DUP Jxxx") for each duplicate.
The entry with the lowest journal number in each group is kept as authoritative.

Usage
-----
    # Preview (no changes):
    python scripts/purge_from_csv.py --csv data/test-client/General.csv

    # Execute:
    python scripts/purge_from_csv.py --csv data/test-client/General.csv ^
        --sai "R:\\Concetta Enterprises Inc\\2026.SAI" ^
        --user sysadmin --password "Rivera1949#" --execute
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Sage 50 display account code → internal lId for Concetta Enterprises
# (matches CONCETTA_ACCOUNT_MAP; bank displays as 1100 but our map key is 1060→11000000)
_DISPLAY_TO_LID: dict[str, str] = {
    "1100": "11000000",   # Bank
    "5155": "51550000",   # Car Lease
    "5200": "52000000",   # Bank Charges & Interest
    "5400": "54000000",   # Insurance
    "5700": "57000000",   # Visa
    "5725": "57250000",   # AMEX
    "5750": "57500000",   # Mastercard
    "5800": "58000000",   # Rent
    "5850": "58500000",   # Wages & Benefits
    "5900": "59000000",   # Suspense
}


def _lid(display_code: str) -> str:
    return _DISPLAY_TO_LID.get(display_code, display_code)


def _parse_amount(s: str) -> Decimal:
    s = s.strip().replace(",", "").replace("-", "").replace("$", "")
    if not s:
        return Decimal("0")
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal("0")


def _jno_int(jno: str) -> int:
    """'J98' → 98; used for sort-to-keep-lowest."""
    s = jno.lstrip("Jj")
    return int(s) if s.isdigit() else 0


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------

def parse_gl_csv(path: str) -> list[dict]:
    """Parse a Sage 50 General Journal CSV export into a list of entry dicts.

    Each dict has:
        journal_no  str   "J98"
        date        str   "YYYY-MM-DD"
        description str
        debit_acct  str   Sage 50 display account code
        credit_acct str   Sage 50 display account code
        amount      Decimal
    """
    entries: list[dict] = []
    current: dict | None = None

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if len(row) < 4:
                continue

            col0 = row[0].strip()

            # Journal header row: first column is a date "MM/DD/YYYY"
            if col0 and "/" in col0:
                parts = col0.split("/")
                if len(parts) == 3 and len(parts[2]) == 4:
                    # Flush previous entry
                    if current and current.get("amount") and current.get("debit_acct") and current.get("credit_acct"):
                        entries.append(current)
                    date_iso = f"{parts[2]}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
                    current = {
                        "journal_no":  row[1].strip(),
                        "date":        date_iso,
                        "description": row[3].strip() if len(row) > 3 else "",
                        "debit_acct":  None,
                        "credit_acct": None,
                        "amount":      None,
                    }
                    continue

            # Account line: col 4 = account number (all digits), col 6/7 = debit/credit
            if current is not None and len(row) >= 8:
                acct = row[4].strip()
                if acct and acct.isdigit():
                    debit_str  = row[6].strip()
                    credit_str = row[7].strip()
                    if debit_str and debit_str != "-":
                        amt = _parse_amount(debit_str)
                        if amt > 0:
                            current["debit_acct"] = acct
                            current["amount"]     = amt
                    elif credit_str and credit_str != "-":
                        amt = _parse_amount(credit_str)
                        if amt > 0 and current.get("credit_acct") is None:
                            current["credit_acct"] = acct

    # Flush last entry
    if current and current.get("amount") and current.get("debit_acct") and current.get("credit_acct"):
        entries.append(current)

    return entries


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def find_duplicates(entries: list[dict]) -> list[list[dict]]:
    """Return groups of duplicate entries.

    Duplicates share the same (date, description[:39], debit_acct, amount).
    Each group is sorted ascending by journal number; the first element is kept.
    """
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for e in entries:
        key = (
            e["date"],
            e["description"][:39],
            e["debit_acct"] or "",
            str(e["amount"]),
        )
        by_key[key].append(e)

    result = []
    for group in by_key.values():
        if len(group) > 1:
            result.append(sorted(group, key=lambda e: _jno_int(e["journal_no"])))
    return sorted(result, key=lambda g: _jno_int(g[0]["journal_no"]))


# ---------------------------------------------------------------------------
# Reversal builder
# ---------------------------------------------------------------------------

def build_reversal(entry: dict) -> dict:
    """Equal-and-opposite entry for one duplicate journal entry.

    Original:  Dr debit_acct  /  Cr credit_acct
    Reversal:  Dr credit_acct /  Cr debit_acct
    """
    jno     = entry["journal_no"]
    comment = f"VOID DUP {jno}"[:39]
    amount  = float(entry["amount"])
    return {
        "date":    entry["date"],
        "source":  "RVRSL",
        "comment": comment,
        "lines": [
            {
                "account_id": _lid(entry["credit_acct"]),
                "debit":      amount,
                "credit":     0.0,
                "comment":    comment,
            },
            {
                "account_id": _lid(entry["debit_acct"]),
                "debit":      0.0,
                "credit":     amount,
                "comment":    comment,
            },
        ],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reverse duplicate BNK entries identified from a Sage 50 GL CSV export",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--csv",      required=True,  help="Path to exported General Journal CSV")
    parser.add_argument("--sai",      default=None,   help="Sage 50 .SAI file (falls back to Secret Manager)")
    parser.add_argument("--user",     default=None,   help="Sage 50 user (default: sysadmin)")
    parser.add_argument("--password", default=None,   help="Sage 50 password")
    parser.add_argument("--execute",  action="store_true",
                        help="Post reversals. Omit for preview-only (safe default).")
    args = parser.parse_args()

    print("\nVTX-OS Duplicate Purge — CSV mode")
    print(f"  CSV:  {args.csv}")
    print(f"  Mode: {'EXECUTE — reversing entries WILL be posted' if args.execute else 'PREVIEW — no changes will be made'}")
    print()

    # ── Parse ─────────────────────────────────────────────────────────────────
    entries = parse_gl_csv(args.csv)
    print(f"Parsed {len(entries)} journal entries from CSV")

    # ── Detect ────────────────────────────────────────────────────────────────
    dup_groups = find_duplicates(entries)

    if not dup_groups:
        print("\nNo duplicates found.")
        return

    total_to_reverse = sum(len(g) - 1 for g in dup_groups)
    print(f"\nFound {len(dup_groups)} duplicate group(s) — {total_to_reverse} "
          f"entr{'y' if total_to_reverse == 1 else 'ies'} to reverse:\n")

    # ── Preview ───────────────────────────────────────────────────────────────
    reversals: list[dict] = []
    for group in dup_groups:
        keep = group[0]
        dups = group[1:]
        print(f"  {keep['date']}  ${keep['amount']:>12.2f}  {keep['description'][:35]!r}")
        print(f"    KEEP    {keep['journal_no']}")
        for dup in dups:
            print(f"    REVERSE {dup['journal_no']}")
            reversals.append(build_reversal(dup))
        print()

    print(f"{len(reversals)} reversing entr{'y' if len(reversals) == 1 else 'ies'} ready to post.")

    if not args.execute:
        print("\nThis was a preview. Add --execute to post the reversing entries.")
        return

    if not reversals:
        print("\nNothing to post.")
        return

    # ── Post ──────────────────────────────────────────────────────────────────
    print(f"\nPosting {len(reversals)} reversing entries...", flush=True)
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
                print(f"  {r.get('date')}  {r.get('comment')}  error={r.get('error', '?')}")
        sys.exit(1)

    print("\nDone. Export the General Journal again to confirm 0 duplicates remain.")


if __name__ == "__main__":
    main()
