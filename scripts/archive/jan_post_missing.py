"""Post ONLY the 7 genuinely-missing January 2026 Concetta entries.

Selects statement lines whose (date, signed amount) has ledger count 0,
builds balanced drafts, and posts them via the bridge. Pre-existing
duplicates are left untouched. Use --post to actually write; default lists.
"""
from __future__ import annotations

import sys
from collections import Counter
from datetime import date
from decimal import Decimal

from sage50.bank_parser import parse_csv
from agents.bookkeeping import _categorize_concetta
from sage50.bridge_reader import fetch_gl_transactions, post_journal_entries
from agents.journal_entry import _build_drafts, _draft_to_bridge
from sage50.categorization_rules import CONCETTA_ACCOUNT_MAP

SAI = r"R:\Concetta Enterprises Inc\2026.SAI"
CSV = r"R:\Concetta Enterprises Inc\drop\HWY_7___PINEVALLEY-2026-01.csv"
BANK_IDS = {"11000000", "1060", "1100"}

# The 7 confirmed-missing keys (date, signed amount). Hard-coded as a guard so
# this script can ONLY ever post these exact lines.
MISSING_KEYS = {
    ("2026-01-26", "-433.10"),
    ("2026-01-26", "-459.92"),
    ("2026-01-27", "-82.56"),
    ("2026-01-30", "-16.25"),
    ("2026-01-30", "-19.00"),
    ("2026-01-30", "-3.00"),
    ("2026-01-30", "-440.70"),
}


def _key(d: date, amt: Decimal) -> tuple[str, str]:
    return (d.isoformat(), f"{amt:.2f}")


def main() -> None:
    do_post = "--post" in sys.argv

    # Re-verify ledger counts for the 7 keys are still 0 before doing anything.
    gl = fetch_gl_transactions(date(2026, 1, 1), date(2026, 1, 31), sai_file=SAI)
    bnk = [g for g in gl if (g.source or "").upper() == "BNK"]
    by_jrnl: dict = {}
    for g in bnk:
        by_jrnl.setdefault(g.journal_no, []).append(g)
    ledger = Counter()
    for rows in by_jrnl.values():
        d = rows[0].transaction_date
        bank_amt = Decimal("0")
        for r in rows:
            if str(r.account_no) in BANK_IDS:
                bank_amt += (r.debit or Decimal("0")) - (r.credit or Decimal("0"))
        ledger[_key(d, bank_amt)] += 1

    bad = [(k, ledger.get(k, 0)) for k in MISSING_KEYS if ledger.get(k, 0) != 0]
    if bad:
        print("ABORT — one or more target keys already exist in ledger:")
        for k, n in bad:
            print(f"  {k}  count={n}")
        sys.exit(1)
    print("Pre-check OK: all 7 target keys have ledger count 0.\n")

    # Select exactly one statement txn per missing key.
    txns = parse_csv(CSV, account_no="xxxx5443")
    cats = _categorize_concetta(txns, threshold=0.80)
    want = Counter(MISSING_KEYS)  # each x1
    selected = []
    for t in cats:
        k = _key(t.txn_date, t.amount)
        if want.get(k, 0) > 0:
            selected.append(t)
            want[k] -= 1
    leftover = {k: n for k, n in want.items() if n > 0}
    if leftover:
        print("ABORT — could not find statement line(s) for:", leftover)
        sys.exit(1)
    assert len(selected) == 7, f"expected 7, selected {len(selected)}"

    drafts = _build_drafts(selected, "1060")
    wire = [_draft_to_bridge(d, CONCETTA_ACCOUNT_MAP) for d in drafts]

    print("Entries to post:")
    for d in drafts:
        print(f"  {d.entry_date}  {d.debit_line.debit:>10}  "
              f"Dr {d.debit_line.account_no} / Cr {d.credit_line.account_no}  "
              f"'{d.description}'")

    # Verify all account codes resolved (no unmapped 4-digit codes left).
    unmapped = set()
    for w in wire:
        for ln in w["lines"]:
            aid = str(ln["account_id"])
            if len(aid) <= 4:
                unmapped.add(aid)
    if unmapped:
        print("\nABORT — unmapped GL codes:", unmapped)
        sys.exit(1)

    if not do_post:
        print("\n[dry-run] pass --post to write these 7 entries to Sage 50.")
        return

    print("\nPosting to Sage 50 ...")
    res = post_journal_entries(wire, sai_file=SAI)
    print(res)


if __name__ == "__main__":
    main()
