"""
tests/qbo_sandbox_live.py — LIVE end-to-end proof of the QboConnector against
an Intuit sandbox company. Requires vtx-qbo-oauth in Secret Manager (run
scripts/qbo_auth.py first) and ADC for Secret Manager access.

What it does (with cleanup — nothing is left in the sandbox):
  1. validate(): OAuth refresh + account map load
  2. posts one balanced $1.23 journal entry (Dr/Cr on two numbered accounts)
  3. existing_keys() round-trip: the JE we just posted must be found
  4. deletes the JE (QBO soft-delete) — sandbox left clean

    python tests/qbo_sandbox_live.py --realm 9341457251495864
    # optionally pin the accounts (default: first two numbered accounts found)
    python tests/qbo_sandbox_live.py --realm ... --dr 1065 --cr 4020

If validate() reports no AcctNum anywhere: in the sandbox company go to
Settings (gear) -> Account and settings -> Advanced -> Chart of accounts ->
enable "Enable account numbers" + "Show account numbers", then edit two
accounts (e.g. a bank + an income account) and give them numbers like
1065 / 4020.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

_passed = _failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--realm", required=True)
    ap.add_argument("--dr", default=None, help="AcctNum for the debit side")
    ap.add_argument("--cr", default=None, help="AcctNum for the credit side")
    args = ap.parse_args()

    from ledger.base import LedgerEntry, LedgerLine
    from ledger.qbo import QboConnector

    conn = QboConnector(args.realm)

    # 1 — validate (OAuth refresh + account map)
    print("\n1. validate()")
    try:
        conn.validate()
        check("OAuth refresh + account map load", True)
    except Exception as exc:
        check(f"OAuth refresh + account map load — {exc}", False)
        amap = {}
        try:
            amap = conn.account_map()
        except Exception:
            pass
        if not amap:
            print("\n  No numbered accounts. Enable account numbers in the sandbox")
            print("  (gear -> Account and settings -> Advanced -> Chart of accounts),")
            print("  number two accounts (e.g. 1065 bank, 4020 income), re-run.")
        return 1

    amap = conn.account_map()
    print(f"  numbered accounts: {len(amap)} -> {dict(list(amap.items())[:6])}")
    nums = sorted(amap)
    dr = args.dr or nums[0]
    cr = args.cr or (nums[1] if len(nums) > 1 else nums[0])
    check("two distinct numbered accounts available", dr != cr)
    print(f"  using Dr {dr} / Cr {cr}")

    # 2 — post one balanced test JE
    print("\n2. post()")
    marker = f"VTX SANDBOX TEST {date.today().isoformat()}"
    amt = Decimal("1.23")
    entry = LedgerEntry(
        entry_date=date.today(),
        comment=marker,
        lines=[
            LedgerLine(gl_ref=dr, debit=amt, comment=marker),
            LedgerLine(gl_ref=cr, credit=amt, comment=marker),
        ],
    )
    res = conn.post([entry])
    check("posted 1 entry, 0 errors", res.posted == 1 and res.errors == 0)
    je_id = res.results[0]["ref"] if res.results else None
    check("QBO returned a JournalEntry Id", bool(je_id))
    print(f"  JournalEntry Id: {je_id}")

    # 3 — dedupe round-trip
    print("\n3. existing_keys()")
    keys = conn.existing_keys(date.today(), date.today())
    check("posted JE found by existing_keys", conn.key(entry) in keys)

    # 4 — cleanup: delete the test JE (needs current SyncToken)
    print("\n4. cleanup")
    ok = False
    if je_id:
        try:
            data = conn._request("GET", f"journalentry/{je_id}")
            sync = data["JournalEntry"]["SyncToken"]
            conn._request("POST", "journalentry", params={"operation": "delete"},
                          json={"Id": je_id, "SyncToken": sync})
            ok = True
        except Exception as exc:
            print(f"  delete failed: {exc}")
    check("test JE deleted (sandbox left clean)", ok)

    total = _passed + _failed
    print(f"\n{total}/{total} checks: {_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
