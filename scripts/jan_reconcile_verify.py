"""Read-only January 2026 reconciliation for Concetta.

Compares the statement (multiset of date, signed amount) against the BNK
journals already in Sage 50, and lists exactly which statement lines are
still missing from the ledger. Posts nothing.
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import date
from decimal import Decimal

from sage50.bank_parser import parse_csv
from agents.bookkeeping import _categorize_concetta
from sage50.bridge_reader import fetch_gl_transactions

SAI = r"R:\Concetta Enterprises Inc\2026.SAI"
CSV = r"R:\Concetta Enterprises Inc\drop\HWY_7___PINEVALLEY-2026-01.csv"


def _key(d: date, amt: Decimal) -> tuple[str, str]:
    return (d.isoformat(), f"{amt:.2f}")


def main() -> None:
    # Statement side
    txns = parse_csv(CSV, account_no="xxxx5443")
    cats = _categorize_concetta(txns, threshold=0.80)
    stmt = Counter(_key(t.txn_date, t.amount) for t in cats)
    print(f"Statement lines: {len(cats)}")

    # Ledger side — BNK journals for January 2026
    gl = fetch_gl_transactions(date(2026, 1, 1), date(2026, 1, 31), sai_file=SAI)
    bnk = [g for g in gl if (g.source or "").upper() == "BNK"]
    # Each journal has a bank (1060/11000000) leg; signed amount = -(bank debit-credit)
    # Reconstruct signed statement amount from the NON-bank leg is unreliable;
    # use journal net on the bank account instead. The bridge returns one row per
    # detail line; group by journal no.
    by_jrnl: dict = {}
    for g in bnk:
        by_jrnl.setdefault(g.journal_no, []).append(g)

    ledger = Counter()
    BANK_IDS = {"11000000", "1060", "1100"}
    for jno, rows in by_jrnl.items():
        d = rows[0].transaction_date
        bank_amt = Decimal("0")
        for r in rows:
            acct = str(r.account_no)
            if acct in BANK_IDS:
                # debit to bank = money in (+), credit to bank = money out (-)
                bank_amt += (r.debit or Decimal("0")) - (r.credit or Decimal("0"))
        ledger[_key(d, bank_amt)] += 1

    print(f"Ledger BNK journals: {len(by_jrnl)}")

    missing = Counter()
    for k, n in stmt.items():
        have = ledger.get(k, 0)
        if n > have:
            missing[k] = n - have

    dup = Counter()
    for k, have in ledger.items():
        want = stmt.get(k, 0)
        if have > want:
            dup[k] = have - want

    print("\n=== TRULY MISSING (statement > ledger) ===")
    for (d, amt), n in sorted(missing.items()):
        print(f"  {d}  {amt:>12}  x{n}")
    print(f"  total missing lines: {sum(missing.values())}")

    print("\n=== PRE-EXISTING DUPLICATES (ledger > statement) ===")
    for (d, amt), n in sorted(dup.items()):
        print(f"  {d}  {amt:>12}  extra x{n}")
    print(f"  total extra lines: {sum(dup.values())}")


if __name__ == "__main__":
    main()
