"""List every January 2026 BNK journal currently in Sage 50 (read-only)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sage50.bridge_reader import fetch_gl_transactions

SAI = r"R:\Concetta Enterprises Inc\2026.SAI"
BANK_IDS = {"11000000", "1060", "1100"}

gl = fetch_gl_transactions(date(2026, 1, 1), date(2026, 1, 31), sai_file=SAI)
bnk = [g for g in gl if (g.source or "").upper() == "BNK"]
by_jrnl: dict = {}
for g in bnk:
    by_jrnl.setdefault(g.journal_no, []).append(g)

print(f"January 2026 BNK journals: {len(by_jrnl)}\n")
for jno in sorted(by_jrnl, key=lambda x: int(x) if str(x).isdigit() else x):
    rows = by_jrnl[jno]
    d = rows[0].transaction_date
    bank_amt = Decimal("0")
    for r in rows:
        if str(r.account_no) in BANK_IDS:
            bank_amt += (r.debit or Decimal("0")) - (r.credit or Decimal("0"))
    print(f"  J{jno:>4}  {d}  {bank_amt:>12}  '{rows[0].description}'")
