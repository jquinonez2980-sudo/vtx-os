"""
tests/rlelectric_categorization_smoke.py
Offline smoke test for the R.L. Electric Inc. categorization ruleset.
No GCP calls.  All keywords taken from the actual FY2021 General Ledger.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sage50.categorization_rules import RLElectricRuleset, get_ruleset

_passed = _failed = 0


def check(label: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {label}")
    else:
        _failed += 1
        print(f"  [FAIL] {label}")


def main() -> int:
    rs = RLElectricRuleset()

    # (description, amount, expected_gl)
    cases = [
        # -- 5200 Bank Charges & Interest ---------------------------------
        # BMO fee names from actual FY2021 GL entries
        ("PERFORMANCE FEE", Decimal("-15.95"), 5200),
        ("PERFORMANCE PLAN FEE", Decimal("-16.95"), 5200),
        ("PERFORMANCE AND OVERDRAFT FEE", Decimal("-20.95"), 5200),
        ("RETURNED ITEM FEE", Decimal("-48.00"), 5200),
        ("INTEREST PAID", Decimal("-10.14"), 5200),
        # NSF anchored — must NOT fire inside "E-TRANSFER" or "TRANSFER"
        ("NSF FEE", Decimal("-48.00"), 5200),
        (" NSF RETURN", Decimal("-48.00"), 5200),

        # -- 2625 Bank Loan -----------------------------------------------
        ("TD LOAN", Decimal("-160.59"), 2625),
        ("TD LOAN PAYMENT", Decimal("-321.18"), 2625),

        # -- 5600 Telephone & Cellular ------------------------------------
        ("BELL ONE BILL", Decimal("-100.00"), 5600),
        ("VIRGIN", Decimal("-300.00"), 5600),

        # -- 2100 Employee Tax Deduction ----------------------------------
        ("RECEIVER GENERAL PAYROLL", Decimal("-3200.00"), 2100),
        ("RECEIVEUR GENERAL HST", Decimal("-1800.00"), 2100),
        ("CRA PAYMENT", Decimal("-900.00"), 2100),

        # -- 5450 Materials & Supplies ------------------------------------
        # Named suppliers from FY2021 GL
        ("PAUL WOLF ELECTRIC", Decimal("-271.36"), 5450),
        ("PAUL WOLF ELECTR", Decimal("-78.11"), 5450),
        ("WESTON ELECTRIC", Decimal("-219.33"), 5450),
        ("WESTON ELECT", Decimal("-194.25"), 5450),
        ("HUDCO ELECTRIC", Decimal("-246.03"), 5450),
        ("RATEX ELECTRICAL", Decimal("-307.61"), 5450),
        ("WESTBURNE ONTARIO", Decimal("-178.49"), 5450),
        ("SAM SUPPLY", Decimal("-72.37"), 5450),
        ("JENCO", Decimal("-110.64"), 5450),
        ("THE HOME DEPOT", Decimal("-216.27"), 5450),
        ("HOME DEPOT PURCHASE", Decimal("-57.04"), 5450),
        ("RONA", Decimal("-187.07"), 5450),

        # -- 4050 General Revenue -----------------------------------------
        # FY2021 descriptions: DEPOSITS, DEPOSIT, DESPOSITS (typo)
        ("DEPOSITS", Decimal("2800.00"), 4050),
        ("DEPOSIT", Decimal("500.00"), 4050),
        ("DESPOSITS", Decimal("2800.00"), 4050),
        # Deposits must NOT match on negative amounts
        ("DEPOSITS", Decimal("-50.00"), 5900),

        # -- 5700 Travel, Meals & Entertainment ---------------------------
        # FY2021: fuel coded to 5700 alongside food (bookkeeper batching)
        ("ESSO", Decimal("-85.00"), 5700),
        ("SHELL", Decimal("-41.29"), 5700),
        ("PETRO CANADA", Decimal("-95.00"), 5700),
        ("PETRO GAS", Decimal("-60.00"), 5700),
        ("PIONEER", Decimal("-68.00"), 5700),
        ("HUSKY", Decimal("-55.00"), 5700),
        ("TIM HORTONS", Decimal("-8.50"), 5700),
        ("MCDONALD'S", Decimal("-12.75"), 5700),
        ("MCDONALS", Decimal("-10.00"), 5700),
        ("SUBWAY", Decimal("-14.00"), 5700),
        ("PIZZA PIZZA", Decimal("-22.00"), 5700),
        ("LCBO", Decimal("-48.00"), 5700),
        ("THE BEER STORE", Decimal("-36.00"), 5700),
        ("NOFRILLS", Decimal("-85.00"), 5700),

        # -- 2750 Shareholder's Advance (needs review, confidence 0) ------
        ("NETFLIX", Decimal("-21.46"), 2750),
        ("AMAZON PRIME MEMBER", Decimal("-9.03"), 2750),
        ("AMAZON MEMBER", Decimal("-9.03"), 2750),
        ("PLANET FITNESS FEE", Decimal("-9.11"), 2750),
        ("EQUIFAX", Decimal("-19.95"), 2750),
        ("UBER", Decimal("-30.78"), 2750),
        ("E-TFR", Decimal("-71.00"), 2750),
        ("E-TRF", Decimal("-420.00"), 2750),
        ("ABM W\\D", Decimal("-1200.00"), 2750),
        ("ABC W\\D", Decimal("-900.00"), 2750),

        # -- 5900 Suspense ------------------------------------------------
        ("AAR PLUMBING", Decimal("-130.00"), 5900),
        ("UNKNOWN VENDOR XYZ", Decimal("-100.00"), 5900),
        ("AUTOMO EMERGENC", Decimal("-124.30"), 5900),  # not enough history to pin
    ]

    print("=== RLElectric categorization rules (FY2021 GL-derived) ===")
    for desc, amt, expected_gl in cases:
        gl, name, conf = rs.categorize(desc, amt)
        check(f"{desc[:50]:<50} -> GL {gl} ({name})",
              gl == expected_gl)

    print()
    print("=== Ordering edge cases ===")

    # "NSF" inside "TRANSFER" must NOT fire bank-fee rule (falls through to suspense)
    gl, _, _ = rs.categorize("E-TRANSFER FROM CLIENT", Decimal("500.00"))
    check("NSF inside TRANSFER does not trigger bank fee (-> suspense 5900)", gl == 5900)

    gl, _, _ = rs.categorize("E-TRANSFER MISC", Decimal("-200.00"))
    check("E-TRANSFER outgoing (no keyword match) -> suspense 5900", gl == 5900)

    # Telecom before meals — "VIRGIN" must not fall through to meals
    gl, _, _ = rs.categorize("VIRGIN MOBILE", Decimal("-200.00"))
    check("VIRGIN MOBILE -> telecom (5600), not meals", gl == 5600)

    # Paul Wolf before generic Home Depot — both are materials
    gl, _, _ = rs.categorize("PAUL WOLF ELECTRIC SUPPLY", Decimal("-200.00"))
    check("PAUL WOLF ELECTRIC SUPPLY -> materials (5450)", gl == 5450)

    # Revenue positive only
    gl, _, _ = rs.categorize("DEPOSITS", Decimal("-50.00"))
    check("DEPOSITS with negative amount -> suspense (5900)", gl == 5900)

    # Shareholder items route with confidence 0
    _, _, conf = rs.categorize("NETFLIX", Decimal("-21.46"))
    check("NETFLIX confidence = 0 (goes to review queue)", conf == Decimal("0"))

    print()
    print("=== get_ruleset registry ===")
    check("get_ruleset('rlelectric') returns RLElectricRuleset",
          isinstance(get_ruleset("rlelectric"), RLElectricRuleset))
    check("get_ruleset('concetta') is not RLElectricRuleset",
          not isinstance(get_ruleset("concetta"), RLElectricRuleset))

    print()
    total = _passed + _failed
    print(f"{total}/{total} checks: {_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
