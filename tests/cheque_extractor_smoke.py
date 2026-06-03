"""
tests/cheque_extractor_smoke.py
Offline smoke tests for sage50/cheque_extractor.py.

No GCP or real PDFs required.  Synthetic cheque page texts are used as fixtures
that match what Document AI row-reconstruction would produce for TD Bank cheques.

Run:
    python tests/cheque_extractor_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from decimal import Decimal

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from sage50.cheque_extractor import (
    ChequeInfo,
    _is_cheque_page,
    _parse_cheque_page,
    extract_cheque_map,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Clean cheque page — two cheques stacked (typical TD Bank layout)
FIXTURE_TWO_CHEQUES = """\
Concetta Enterprises Inc.  No. 00788
1234 Any Street Toronto ON M9C 1A1
Pay to the order of Rogers Communications Inc.  457.13
Four Hundred Fifty-Seven and 13/100 Dollars
TD Canada Trust  1890-5315443
|| 00788 |

Concetta Enterprises Inc.  No. 00789
1234 Any Street Toronto ON M9C 1A1
Pay to the order of Hydro One Networks Inc.  430.14
Four Hundred Thirty and 14/100 Dollars
TD Canada Trust  1890-5315443
|| 00789 |
"""

# Clean single cheque page
FIXTURE_SINGLE_CHEQUE = """\
Concetta Enterprises Inc.  No. 00793
Pay to the order of City of Toronto  433.10
Four Hundred Thirty-Three and 10/100 Dollars
TD Canada Trust
|| 00793 |
"""

# Transaction (ledger) page — must NOT be classified as a cheque page
FIXTURE_TXN_PAGE = """\
BALANCE FORWARD  JAN30  12,713.96
CHQ#00788-1141529082  457.13  FEB02  12,256.83
CHQ#00789-1141529085  430.14  FEB02  11,826.69
PC MASTRCRD K3L2Q3  75.87  FEB04  11,750.82
MONTHLY PLAN FEE  19.00  FEB27  5,323.64
CLOSING BALANCE  FEB27  5,343.64
"""

# Garbled cheque page — MICR unreadable, payee found only
FIXTURE_GARBLED_MICR = """\
Concetta Enterprises Inc.
Pay to the order of Fido Solutions Inc.  92.26
Ninety-Two and 26/100 Dollars
TD Canada Trust
|: 00420000 :| 18905315443 |I  00801 I|
"""

# Page with no payee — should be ignored
FIXTURE_NO_PAYEE = """\
Concetta Enterprises Inc.  No. 00802
Four Hundred Fifty-Nine and 92/100 Dollars
TD Canada Trust  1890-5315443
"""

# Account summary page (has account number but no "Pay to")
FIXTURE_ACCOUNT_SUMMARY = """\
TD Bank Personal Banking  Statement of Account
Account: 1890-5315443
Statement Period: JAN 30/26 - FEB 27/26
Opening Balance  12,713.96
Total Withdrawals  7,370.30
Total Deposits  0.00
Closing Balance  5,343.64
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

passed = 0
failed = 0


def check(label: str, condition: bool) -> None:
    global passed, failed
    if condition:
        print(f"  PASS  {label}")
        passed += 1
    else:
        print(f"  FAIL  {label}")
        failed += 1


print("=== cheque_extractor_smoke ===\n")

# --- Test 1: page classification ---
print("1. Page classification")
check("txn ledger page: NOT cheque",     not _is_cheque_page(FIXTURE_TXN_PAGE))
check("two-cheque page: IS cheque",      _is_cheque_page(FIXTURE_TWO_CHEQUES))
check("single cheque page: IS cheque",   _is_cheque_page(FIXTURE_SINGLE_CHEQUE))
check("account summary: NOT cheque",     not _is_cheque_page(FIXTURE_ACCOUNT_SUMMARY))
check("no payee page: NOT cheque",       not _is_cheque_page(FIXTURE_NO_PAYEE))

# --- Test 2: parse two-cheque page ---
print("\n2. Two-cheque page parsing")
results = _parse_cheque_page(FIXTURE_TWO_CHEQUES)
check("two cheques found",          len(results) == 2)
check("first cheque_no = 00788",    results[0].cheque_no == "00788")
check("first payee = Rogers",       "Rogers Communications" in results[0].payee)
check("first amount = 457.13",      results[0].amount == Decimal("457.13"))
check("first confidence = 1.0",     results[0].confidence == 1.0)
check("second cheque_no = 00789",   results[1].cheque_no == "00789")
check("second payee = Hydro One",   "Hydro One" in results[1].payee)
check("second amount = 430.14",     results[1].amount == Decimal("430.14"))

# --- Test 3: parse single cheque page ---
print("\n3. Single cheque page parsing")
results = _parse_cheque_page(FIXTURE_SINGLE_CHEQUE)
check("one cheque found",           len(results) == 1)
check("cheque_no = 00793",          results[0].cheque_no == "00793")
check("payee = City of Toronto",    "City of Toronto" in results[0].payee)
check("confidence = 1.0",           results[0].confidence == 1.0)

# --- Test 4: garbled MICR ---
print("\n4. Garbled MICR (confidence < 1.0)")
results = _parse_cheque_page(FIXTURE_GARBLED_MICR)
check("one cheque found",           len(results) >= 1)
if results:
    check("payee = Fido",           "Fido" in results[0].payee)
    check("confidence < 1.0",       results[0].confidence < 1.0)

# --- Test 5: extract_cheque_map ---
print("\n5. extract_cheque_map")
page_texts = [
    FIXTURE_ACCOUNT_SUMMARY,
    FIXTURE_TXN_PAGE,
    FIXTURE_TWO_CHEQUES,
    FIXTURE_SINGLE_CHEQUE,
]
cmap = extract_cheque_map(page_texts)
check("map has 3 entries",          len(cmap) == 3)
check("00788 in map",               "00788" in cmap)
check("00789 in map",               "00789" in cmap)
check("00793 in map",               "00793" in cmap)
check("00788 payee = Rogers",       "Rogers" in cmap["00788"].payee)

# --- Test 6: statement ledger page excluded ---
print("\n6. Statement ledger page excluded from map")
ledger_only = [FIXTURE_TXN_PAGE, FIXTURE_ACCOUNT_SUMMARY]
empty_map = extract_cheque_map(ledger_only)
check("no cheque pages: empty map", len(empty_map) == 0)

# --- Summary ---
print(f"\n{'-' * 40}")
print(f"  {passed + failed} checks: {passed} passed, {failed} failed")
if failed:
    sys.exit(1)
