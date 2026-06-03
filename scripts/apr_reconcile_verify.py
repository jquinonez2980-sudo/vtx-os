"""Read-only April 2026 reconciliation for Concetta.

Compares the statement (multiset of date, signed amount) against the BNK
journals in Sage 50. Posts nothing.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal

from sage50.bank_parser import parse_csv
from agents.bookkeeping import _categorize_concetta
from sage50.bridge_reader import fetch_gl_transactions

SAI = r"R:\Concetta Enterprises Inc\2026.SAI"
CSV = r"R:\Concetta Enterprises Inc\drop\JCA2099948-0045181-19063-0003-0001-00-2026-04.csv"
BANK_IDS = {"11000000", "1060", "1100"}


def _key(d: date, amt: Decimal) -> tuple[str, str]:
    return (d.isoformat(), f"{amt:.2f}")


def main() -> None:
    txns = parse_csv(CSV, account_no="xxxx5443")
    cats = _categorize_concetta(txns, threshold=0.80)
    stmt = Counter(_key(t.txn_date, t.amount) for t in cats)
    print(f"Statement lines: {len(cats)}")

    gl = fetch_gl_transactions(date(2026, 4, 1), date(2026, 4, 30), sai_file=SAI)
    bnk = [g for g in gl if (g.source or "").upper() == "BNK"]
    by_jrnl: dict = {}
    for g in bnk:
        by_jrnl.setdefault(g.journal_no, []).append(g)

    ledger = Counter()
    for jno, rows in by_jrnl.items():
        d = rows[0].transaction_date
        bank_amt = Decimal("0")
        for r in rows:
            if str(r.account_no) in BANK_IDS:
                bank_amt += (r.debit or Decimal("0")) - (r.credit or Decimal("0"))
        ledger[_key(d, bank_amt)] += 1

    print(f"Ledger BNK journals: {len(by_jrnl)}")

    missing = Counter()
    for k, n in stmt.items():
        if n > ledger.get(k, 0):
            missing[k] = n - ledger.get(k, 0)
    dup = Counter()
    for k, have in ledger.items():
        if have > stmt.get(k, 0):
            dup[k] = have - stmt.get(k, 0)

    print("\n=== TRULY MISSING (statement > ledger) ===")
    for (d, amt), n in sorted(missing.items()):
        print(f"  {d}  {amt:>12}  x{n}")
    print(f"  total missing lines: {sum(missing.values())}")

    print("\n=== PRE-EXISTING DUPLICATES (ledger > statement) ===")
    for (d, amt), n in sorted(dup.items()):
        print(f"  {d}  {amt:>12}  extra x{n}")
    print(f"  total extra lines: {sum(dup.values())}")

    clean = not missing and not dup and len(cats) == len(by_jrnl)
    print(f"\nRECONCILIATION: {'CLEAN' if clean else 'NEEDS REVIEW'}")


if __name__ == "__main__":
    main()
