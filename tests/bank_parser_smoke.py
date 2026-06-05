"""
tests/bank_parser_smoke.py — offline checks for sage50/bank_parser.py

Focus: the running-balance column must be read regardless of header variant
("Balance" vs "Balance ($)"). Balance is the project's ground truth for
sign/amount verification; silently dropping it is the bug class this guards.

Run:  python tests/bank_parser_smoke.py
"""
from __future__ import annotations

import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sage50.bank_parser import parse_csv

_passed = _failed = 0


def check(label: str, cond: bool) -> None:
    global _passed, _failed
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if cond:
        _passed += 1
    else:
        _failed += 1


def _write(tmp: Path, header_balance: str) -> Path:
    p = tmp / f"stmt_{header_balance.replace(' ', '_').replace('(', '').replace(')', '').replace('$', 'd')}.csv"
    p.write_text(
        "Date,Description,Withdrawals ($),Deposits ($)," + header_balance + "\n"
        "2025-12-02,DEPOSIT FROM CLIENT,,1000.00,5000.00\n"
        "2025-12-03,PAYMENT TO VENDOR,400.00,,4600.00\n",
        encoding="utf-8",
    )
    return p


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)

        # Balance read under both header spellings
        for header in ("Balance", "Balance ($)"):
            txns = parse_csv(_write(tmp, header), account_no="xxxx1234")
            check(f"{header!r}: 2 transactions parsed", len(txns) == 2)
            check(f"{header!r}: balance populated on row 0",
                  txns[0].balance == Decimal("5000.00"))
            check(f"{header!r}: balance populated on row 1",
                  txns[1].balance == Decimal("4600.00"))
            # Sign convention: deposit positive, withdrawal negative
            check(f"{header!r}: deposit -> +1000.00", txns[0].amount == Decimal("1000.00"))
            check(f"{header!r}: withdrawal -> -400.00", txns[1].amount == Decimal("-400.00"))
            # Balance chain reconciles to the cent
            check(f"{header!r}: balance chain reconciles",
                  (txns[1].balance - txns[0].balance) == txns[1].amount)

    total = _passed + _failed
    print(f"\n{total}/{total} checks: {_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
