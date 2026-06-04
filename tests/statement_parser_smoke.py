"""
tests/statement_parser_smoke.py
Offline checks for the bank_statement_ocr_parser correctness fixes:
  - _apply_year_rollover : Dec->Jan straddle gets the right calendar years
  - _SUMMARY_LINE_RE     : statement total/summary lines are recognised (skipped)
  - anchor_year_to_period: a wrong inferred year is re-anchored to the period
"""
from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sage50.bank_statement_ocr_parser import (
    _Txn, _SUMMARY_LINE_RE, _apply_year_rollover, anchor_year_to_period,
)

_p = _f = 0


def check(label, cond):
    global _p, _f
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if cond: _p += 1
    else: _f += 1


def _txn(d):
    return _Txn(d, "x", Decimal("1"), Decimal("0"), None)


def main() -> int:
    # 1 — year rollover: statement Dec->Jan, all stamped with closing year 2025
    t = [_txn(date(2025, 12, 9)), _txn(date(2025, 12, 31)),
         _txn(date(2025, 1, 2)), _txn(date(2025, 1, 7))]
    _apply_year_rollover(t, 2025)
    check("rollover: December rows -> 2024",
          [x.txn_date.year for x in t] == [2024, 2024, 2025, 2025])

    # 2 — single-year statement untouched
    s = [_txn(date(2025, 3, 1)), _txn(date(2025, 4, 1))]
    _apply_year_rollover(s, 2025)
    check("rollover: single-year untouched", all(x.txn_date.year == 2025 for x in s))

    # 3 — summary lines recognised
    for ln in ("Closing totals 18,756.35 13,622.91", "Total deposits 100.00",
               "Closing balance 50.00", "Total withdrawals 9.99"):
        if not _SUMMARY_LINE_RE.search(ln):
            check(f"summary matches {ln!r}", False); break
    else:
        check("summary lines all matched", True)
    check("summary does NOT match a real txn",
          not _SUMMARY_LINE_RE.search("INTERAC e-Transfer Received"))

    # 4 — anchor a mis-inferred 2026 statement back to 2025 period
    d = [_txn(date(2026, 11, 14)), _txn(date(2026, 12, 2))]
    off = anchor_year_to_period(d, 2025)
    check("anchor: shift is -1 year", off == -1)
    check("anchor: years now 2025",
          [x.txn_date.year for x in d] == [2025, 2025])
    check("anchor: months/days preserved",
          [(x.txn_date.month, x.txn_date.day) for x in d] == [(11, 14), (12, 2)])

    # 5 — anchor is a no-op when already aligned
    e = [_txn(date(2025, 6, 1))]
    check("anchor: aligned -> offset 0", anchor_year_to_period(e, 2025) == 0)

    print(f"\n{_p} passed, {_f} failed")
    return 1 if _f else 0


if __name__ == "__main__":
    raise SystemExit(main())
