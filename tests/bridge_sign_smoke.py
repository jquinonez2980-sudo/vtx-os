"""
tests/bridge_sign_smoke.py
M0.3 — Documents the balance-impact sign decode bugs in _map_gl,
fetch_trial_balance, and fetch_tax_summary for credit-normal accounts.

OFFLINE: mocks _run_bridge and _get_creds — no bridge executable, no GCP.

Background (Session-23 discovery):
    dAmount from Sage50Bridge is a BALANCE-IMPACT sign, not debit/credit polarity:
      Debit-normal  (1xxx asset, 5xxx/6xxx expense): positive = debit,  negative = credit
      Credit-normal (2xxx liability, 3xxx equity, 4xxx revenue): positive = credit, negative = debit

    The three functions _map_gl, fetch_trial_balance, fetch_tax_summary all use the
    naive split (positive=debit / amount<0 triggers credit path) and are wrong for
    credit-normal accounts.  M1.2 will add decode_dr_cr(lAcctId, dAmount, coa_funcs)
    to fix all three.

Check legend:
  PASS   — correct behaviour
  FAIL   — unexpected regression (exit 1)
  XFAIL  — known bug pending M1.2; exit 0 (expected failure)
  XPASS  — M1.2 has landed; remove the xfail= markers (exit 1 as a reminder)

Exit 0 when every check is PASS or XFAIL.
Exit 1 on any FAIL or XPASS.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# Helpers to build synthetic bridge rows
# ---------------------------------------------------------------------------

def _lId(display: str) -> str:
    """4-digit display code → 8-digit Sage 50 lId."""
    return str(int(display) * 10000)


def _gl_row(lAcctId: str, dAmount: str, comment: str = "") -> dict:
    return {
        "lJEntID":    "1001",
        "txnDate":    "2025-12-15",
        "sSource":    "BNK",
        "hdrComment": "TEST ENTRY",
        "nLineNum":   "1",
        "lAcctId":    lAcctId,
        "dAmount":    dAmount,
        "szComment":  comment,
        "acctName":   "",
    }


def _coa_row(lId: str, cFunc: str, sName: str) -> dict:
    return {"lId": lId, "cFunc": cFunc, "sName": sName}


# Accounts under test
#   Debit-normal:  1xxx (asset), 5xxx/6xxx (expense)
#   Credit-normal: 4xxx (revenue), 2xxx (liability), 3xxx (equity)

_ACCT_1060 = _lId("1060")   # bank account   — asset,     debit-normal
_ACCT_4100 = _lId("4100")   # sales revenue  — revenue,   credit-normal
_ACCT_5100 = _lId("5100")   # salaries       — expense,   debit-normal
_ACCT_2100 = _lId("2100")   # HST payable    — liability, credit-normal


# ---------------------------------------------------------------------------
# Check registry
# ---------------------------------------------------------------------------

_checks: list[tuple[str, bool, bool]] = []   # (label, passed, is_xfail)


def _check(label: str, value: bool, *, xfail: bool = False) -> None:
    _checks.append((label, value, xfail))
    if xfail:
        mark = "XPASS" if value else "XFAIL"
    else:
        mark = "PASS" if value else "FAIL"
    print(f"  [{mark}] {label}")


# ---------------------------------------------------------------------------
# Import functions under test
# ---------------------------------------------------------------------------

from sage50.bridge_reader import _map_gl, fetch_tax_summary, fetch_trial_balance


# ===========================================================================
# Suite 1 — _map_gl: pure function, no bridge call needed
# ===========================================================================

print("\n--- Suite 1: _map_gl sign decode ---")

# 1a — CONTROL: asset 1060, positive dAmount → debit (correct both before and after M1.2)
_r1a = _map_gl(_gl_row(_ACCT_1060, "500.00"))
_check("1a asset  +500 → debit=500",    _r1a.debit  == Decimal("500.00"))
_check("1a asset  +500 → credit=0",     _r1a.credit == Decimal("0"))

# 1b — CONTROL: asset 1060, negative dAmount → credit
_r1b = _map_gl(_gl_row(_ACCT_1060, "-200.00"))
_check("1b asset  -200 → debit=0",      _r1b.debit  == Decimal("0"))
_check("1b asset  -200 → credit=200",   _r1b.credit == Decimal("200.00"))

# 1c — CONTROL: expense 5100, positive dAmount → debit
_r1c = _map_gl(_gl_row(_ACCT_5100, "300.00"))
_check("1c expense +300 → debit=300",   _r1c.debit  == Decimal("300.00"))
_check("1c expense +300 → credit=0",    _r1c.credit == Decimal("0"))

# 1d — XFAIL: revenue 4100, positive dAmount = CREDIT (balance-impact convention)
#   Current: debit=1000, credit=0    (wrong — _map_gl ignores account nature)
#   Fixed:   debit=0,    credit=1000
_r1d = _map_gl(_gl_row(_ACCT_4100, "1000.00"))
_check(
    "1d revenue +1000 → debit=0  (credit-normal; positive = credit)",
    _r1d.debit == Decimal("0"),
    xfail=True,
)
_check(
    "1d revenue +1000 → credit=1000",
    _r1d.credit == Decimal("1000.00"),
    xfail=True,
)

# 1e — XFAIL: liability 2100, positive dAmount = CREDIT
_r1e = _map_gl(_gl_row(_ACCT_2100, "800.00"))
_check(
    "1e liability +800 → debit=0  (credit-normal; positive = credit)",
    _r1e.debit == Decimal("0"),
    xfail=True,
)
_check(
    "1e liability +800 → credit=800",
    _r1e.credit == Decimal("800.00"),
    xfail=True,
)


# ===========================================================================
# Suite 2 — fetch_trial_balance: mocks _get_creds + _run_bridge
# ===========================================================================

print("\n--- Suite 2: fetch_trial_balance sign decode ---")

_TB_COA = [
    _coa_row(_ACCT_1060, "A", "Bank - TD Chequing"),
    _coa_row(_ACCT_4100, "R", "Sales Revenue"),
    _coa_row(_ACCT_5100, "X", "Salaries Expense"),
    _coa_row(_ACCT_2100, "C", "HST Payable"),
]
_TB_GL = [
    # Bank 1060: net +3000 → debit balance (correct before and after M1.2)
    _gl_row(_ACCT_1060, "1000.00"),
    _gl_row(_ACCT_1060, "1500.00"),
    _gl_row(_ACCT_1060,  "500.00"),
    # Revenue 4100: net +2500 (balance-impact = credit balance)
    #   Current: net > 0 → debit=2500, credit=0    (WRONG)
    #   Fixed:   net > 0 for credit-normal → debit=0, credit=2500
    _gl_row(_ACCT_4100, "2000.00"),
    _gl_row(_ACCT_4100,  "500.00"),
    # Expense 5100: net +400 → debit balance (correct)
    _gl_row(_ACCT_5100,  "400.00"),
]

_tb_returns = iter([_TB_COA, _TB_GL])

with patch("sage50.bridge_reader._get_creds", return_value=("dummy.sai", "u", "p")), \
     patch("sage50.bridge_reader._run_bridge", side_effect=lambda *a, **kw: next(_tb_returns)):
    _tb = {l.account_no: l for l in fetch_trial_balance()}

# 2a — CONTROL: asset/bank debit balance
_check("2a bank  1060 present",       "1060" in _tb)
_check("2a bank  1060 debit=3000",    "1060" in _tb and _tb["1060"].debit  == Decimal("3000.00"))
_check("2a bank  1060 credit=0",      "1060" in _tb and _tb["1060"].credit == Decimal("0"))

# 2b — CONTROL: expense debit balance
_check("2b expense 5100 debit=400",   "5100" in _tb and _tb["5100"].debit  == Decimal("400.00"))
_check("2b expense 5100 credit=0",    "5100" in _tb and _tb["5100"].credit == Decimal("0"))

# 2c — XFAIL: revenue net positive → credit balance (not debit)
#   Current: debit=2500, credit=0    (wrong; `if net > 0: debit=net` is blind to account nature)
#   Fixed:   debit=0,    credit=2500
_check(
    "2c revenue 4100 debit=0    (positive net = credit balance for credit-normal)",
    "4100" in _tb and _tb["4100"].debit == Decimal("0"),
    xfail=True,
)
_check(
    "2c revenue 4100 credit=2500",
    "4100" in _tb and _tb["4100"].credit == Decimal("2500.00"),
    xfail=True,
)


# ===========================================================================
# Suite 3 — fetch_tax_summary: mocks _get_creds + _run_bridge
# ===========================================================================

print("\n--- Suite 3: fetch_tax_summary sign decode ---")

_TS_COA = [
    _coa_row(_ACCT_4100, "R", "Sales Revenue"),
    _coa_row(_ACCT_5100, "X", "Advertising Expense"),
]
_TS_GL = [
    # Revenue 4100: 2 sales → positive dAmount (balance-impact = credit)
    #   Current: `if amount < 0` catches REVERSALS only → taxable_sales = 0  (WRONG)
    #   Fixed:   `if amount > 0` catches normal credits  → taxable_sales = 5000
    _gl_row(_ACCT_4100, "3000.00"),
    _gl_row(_ACCT_4100, "2000.00"),
    # Expense 5100: 1 purchase → positive dAmount (debit-normal; debit = purchase)
    #   Current: `if amount > 0` — correct
    _gl_row(_ACCT_5100,  "800.00"),
]

_ts_returns = iter([_TS_COA, _TS_GL])

with patch("sage50.bridge_reader._get_creds", return_value=("dummy.sai", "u", "p")), \
     patch("sage50.bridge_reader._run_bridge", side_effect=lambda *a, **kw: next(_ts_returns)):
    _ts = fetch_tax_summary(
        date(2025, 12, 1), date(2025, 12, 31),
        tax_code="H", tax_rate="0.13",
    )

# 3a — CONTROL: expense purchases captured correctly (debit-normal, `amount > 0`)
_check("3a taxable_purchases == 800",     _ts["taxable_purchases"] == Decimal("800.00"))
_check("3a itc_claimed       == 104.00",  _ts["itc_claimed"]       == Decimal("104.00"))

# 3b — XFAIL: revenue credits → taxable_sales
#   Current: taxable_sales = 0      (wrong — `amount < 0` misses normal revenue credits)
#   Fixed:   taxable_sales = 5000
_check(
    "3b taxable_sales == 5000  (revenue credits are positive dAmount)",
    _ts["taxable_sales"] == Decimal("5000.00"),
    xfail=True,
)

# 3c — XFAIL: net_tax = 5000*0.13 - 800*0.13 = 650 - 104 = 546
_check(
    "3c net_tax == 546.00  (depends on correct taxable_sales)",
    _ts["net_tax"] == Decimal("546.00"),
    xfail=True,
)


# ===========================================================================
# Results
# ===========================================================================

n_pass  = sum(1 for _, v, xf in _checks if     v and not xf)
n_xfail = sum(1 for _, v, xf in _checks if not v and     xf)
n_fail  = sum(1 for _, v, xf in _checks if not v and not xf)
n_xpass = sum(1 for _, v, xf in _checks if     v and     xf)
total   = len(_checks)

print(f"\n{'=' * 60}")
print("bridge_sign_smoke — M0.3 balance-impact sign audit")
print(f"{'=' * 60}")
print(f"  PASS:  {n_pass:>2}")
print(f"  XFAIL: {n_xfail:>2}  (known bugs — pending M1.2)")
print(f"  FAIL:  {n_fail:>2}  (unexpected regressions)")
print(f"  XPASS: {n_xpass:>2}  (M1.2 landed — remove xfail= markers)")
print(f"  Total: {total:>2}")

if n_fail:
    print("\nUnexpected FAIL (regressions):")
    for lbl, v, xf in _checks:
        if not v and not xf:
            print(f"  FAIL: {lbl}")

if n_xpass:
    print("\nXPASS (M1.2 fixed these — promote to PASS and remove xfail=):")
    for lbl, v, xf in _checks:
        if v and xf:
            print(f"  XPASS: {lbl}")

if n_fail or n_xpass:
    sys.exit(1)

print("\n0 regressions. All known bugs are documented as XFAIL.")
