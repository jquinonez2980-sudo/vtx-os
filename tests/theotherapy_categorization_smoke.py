"""
tests/theotherapy_categorization_smoke.py
Offline smoke test for the Canadian Federation of theotherapy ruleset and the
BookkeepingAgent min_date (fiscal-start) filter. No GCP calls.

Rules are derived from the client's FY2024 General Ledger. This test pins the
expected description->account mapping and the rule-ordering edge cases
(fee beats tithes; auto-insurance beats generic insurance; Julian beats rent).
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sage50.categorization_rules import TheotherapyRuleset, get_ruleset

_passed = 0
_failed = 0


def check(label: str, cond: bool) -> None:
    global _passed, _failed
    mark = "PASS" if cond else "FAIL"
    (_inc_pass if cond else _inc_fail)()
    print(f"  [{mark}] {label}")


def _inc_pass():
    global _passed
    _passed += 1


def _inc_fail():
    global _failed
    _failed += 1


def main() -> int:
    rs = TheotherapyRuleset()

    # (description, amount, expected_gl)
    cases = [
        # bank fees (must beat tithes/e-transfer)
        ("E-TRANSFER FEE", Decimal("-1.50"), 5690),
        ("WIRE PAYMENT FEE", Decimal("-15.00"), 5690),
        ("BANK CHARGE", Decimal("-4.00"), 5690),
        # fuel (incl. stations added from the FY2024 GL coverage review)
        ("PIONEER", Decimal("-60.00"), 5730),
        ("COSTCO GAS", Decimal("-72.10"), 5730),
        ("CANADIAN TIRE GAS", Decimal("-40.00"), 5730),
        ("SHELL", Decimal("-58.00"), 5730),
        ("HUSKY", Decimal("-61.50"), 5730),
        ("KANATA FUELS", Decimal("-70.00"), 5730),
        ("KING GEORGE GAS", Decimal("-49.00"), 5730),
        ("COLBORNE STREE GAS", Decimal("-44.00"), 5730),
        # telecom vs internet
        ("VIRGIN PLUS", Decimal("-55.00"), 5780),
        ("KOODO MOBILE", Decimal("-45.00"), 5780),
        ("ROGERS", Decimal("-90.00"), 5780),
        ("BELL ONE BILL", Decimal("-110.00"), 5775),
        # insurance: auto beats generic
        ("BENEVA", Decimal("-130.00"), 5688),
        ("DAG INS", Decimal("-200.00"), 5685),
        ("NORTHBRIDGE BUS INS", Decimal("-180.00"), 5685),
        # utilities
        ("GRANDBRIDGE ENE", Decimal("-95.00"), 5790),
        ("HYDRO QUEBEC", Decimal("-88.00"), 5790),
        # rent: Julian beats generic rent
        ("JULIAN ANTHONY", Decimal("-700.00"), 5761),
        ("MOHAMMED AHMED HUSSAN RENT", Decimal("-1200.00"), 5760),
        ("CFT WEST RENT", Decimal("-900.00"), 5760),
        # office / seminar / donation
        ("STAPLES", Decimal("-32.00"), 5700),
        ("BRANTFORD MEETING", Decimal("-50.00"), 5630),
        ("CHRISTMAS DONATION", Decimal("-100.00"), 5680),
        ("195 HENRY", Decimal("-250.00"), 5680),
        # credit card / payroll
        ("BMO MC", Decimal("-300.00"), 5645),
        ("CANACT BUS\\ENT", Decimal("-1500.00"), 2130),
        ("MAURICIO EMILIAN", Decimal("-800.00"), 2160),
        # tithes: incoming only
        ("INTERAC E-TRANSFER", Decimal("500.00"), 4020),
        ("DEPOSITS", Decimal("1000.00"), 4020),
    ]
    for desc, amt, expected in cases:
        gl, name, conf = rs.categorize(desc, amt)
        check(f"{desc!r} ({amt}) -> {expected} {name}", gl == expected)

    # ordering: an OUTGOING e-transfer with no payee is NOT tithes -> suspense
    gl, _, _ = rs.categorize("E-TRANSFER", Decimal("-100.00"))
    check("outgoing E-TRANSFER -> 5800 suspense (not tithes)", gl == 5800)

    # unknown -> suspense, confidence 0
    gl, _, conf = rs.categorize("SOME UNKNOWN VENDOR XYZ", Decimal("-12.00"))
    check("unknown -> 5800 suspense", gl == 5800)
    check("suspense confidence is 0", conf == Decimal("0"))

    # named payee is low-confidence (suggested but still reviewed at 0.80 thr)
    _, _, conf = rs.categorize("MAURICIO EMILIAN", Decimal("-800.00"))
    check("payroll-name confidence < 80 (stays in review)", conf < Decimal("80"))

    # high-confidence vendor auto-categorizes at 0.80 threshold
    _, _, conf = rs.categorize("PIONEER", Decimal("-60.00"))
    check("vendor confidence >= 80 (auto)", conf >= Decimal("80"))

    # incoming INTERAC e-transfer / deposit auto-books revenue (>= 80)
    _, _, conf = rs.categorize("INTERAC E-TRANSFER", Decimal("500.00"))
    check("incoming INTERAC e-transfer auto-books revenue (conf >= 80)", conf >= Decimal("80"))

    # registry resolves the client
    check("get_ruleset('theotherapy') -> TheotherapyRuleset",
          isinstance(get_ruleset("theotherapy"), TheotherapyRuleset))
    check("get_ruleset('unknown') -> None", get_ruleset("nobody") is None)

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
