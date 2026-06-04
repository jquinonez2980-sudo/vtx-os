"""Compute the January 2026 keep/delete plan (read-only).

For each statement (date, signed amount) line, keep the lowest-numbered
matching BNK journal and mark the rest as duplicates to remove. Any ledger
journal whose (date, amount) is not on the statement is removed entirely.
Posts/deletes nothing — prints the plan only.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sage50.bank_parser import parse_csv
from agents.bookkeeping import _categorize_concetta
from sage50.bridge_reader import fetch_gl_transactions

SAI = r"R:\Concetta Enterprises Inc\2026.SAI"
CSV = r"R:\Concetta Enterprises Inc\drop\HWY_7___PINEVALLEY-2026-01.csv"
BANK_IDS = {"11000000", "1060", "1100"}


def _key(d: date, amt: Decimal) -> tuple[str, str]:
    return (d.isoformat(), f"{amt:.2f}")


def _jno_int(j: str) -> int:
    s = str(j).lstrip("Jj")
    return int(s) if s.isdigit() else 0


# Statement side: how many of each key we should keep
txns = parse_csv(CSV, account_no="xxxx5443")
cats = _categorize_concetta(txns, threshold=0.80)
want = defaultdict(int)
for t in cats:
    want[_key(t.txn_date, t.amount)] += 1

# Ledger side: group BNK journals by key
gl = fetch_gl_transactions(date(2026, 1, 1), date(2026, 1, 31), sai_file=SAI)
bnk = [g for g in gl if (g.source or "").upper() == "BNK"]
by_jrnl: dict = defaultdict(list)
for g in bnk:
    by_jrnl[g.journal_no].append(g)

jrnl_info: dict = {}
ledger_by_key: dict = defaultdict(list)
for jno, rows in by_jrnl.items():
    d = rows[0].transaction_date
    bank_amt = Decimal("0")
    for r in rows:
        if str(r.account_no) in BANK_IDS:
            bank_amt += (r.debit or Decimal("0")) - (r.credit or Decimal("0"))
    k = _key(d, bank_amt)
    jrnl_info[jno] = (d, bank_amt, rows[0].description, k)
    ledger_by_key[k].append(jno)

keep, delete = [], []
for k, jnos in ledger_by_key.items():
    jnos_sorted = sorted(jnos, key=_jno_int)
    n_keep = want.get(k, 0)
    keep.extend(jnos_sorted[:n_keep])
    delete.extend(jnos_sorted[n_keep:])

keep_set = set(keep)
print(f"Total BNK journals: {len(by_jrnl)}   KEEP: {len(keep)}   DELETE: {len(delete)}\n")

print("=== DELETE (duplicates) ===")
for jno in sorted(delete, key=_jno_int):
    d, amt, desc, k = jrnl_info[jno]
    print(f"  J{jno:>4}  {d}  {amt:>12}  '{desc}'")

print("\n=== KEEP (one per statement line) ===")
for jno in sorted(keep, key=_jno_int):
    d, amt, desc, k = jrnl_info[jno]
    print(f"  J{jno:>4}  {d}  {amt:>12}  '{desc}'")
