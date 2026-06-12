"""
tests/bridge_sign_smoke.py
M0.3/M1.2 — balance-impact sign decode: verifies decode_dr_cr() correctly
handles credit-normal accounts in _map_gl, fetch_trial_balance, and
fetch_tax_summary.

OFFLINE: mocks _run_bridge and _get_creds — no bridge executable, no GCP.

Background (Session-23 discovery):
    dAmount from Sage50Bridge is a BALANCE-IMPACT sign, not debit/credit polarity:
      Debit-normal  (cFunc A/B/C/X/Y — asset/expense): positive = debit,  negative = credit
      Credit-normal (cFunc L/M/E/F/R/I/O — liability/equity/revenue): positive = credit, negative = debit

    _map_gl, fetch_trial_balance, and fetch_tax_summary all previously used a naive
    positive=debit split, producing wrong outputs for credit-normal accounts.
    M1.2 added decode_dr_cr(lAcctId, dAmount, coa_funcs) to fix all three.

Exit 0 when all checks pass.  Exit 1 on any failure.
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


# Accounts under test — cFunc values from _COA_TYPE:
#   A/B/C = Asset (debit-normal)   X/Y = Expense (debit-normal)
#   L/M   = Liability (credit-normal)   E/F = Equity (credit-normal)
#   R/I/O = Revenue (credit-normal)

_ACCT_1060 = _lId("1060")   # bank account   — cFunc "A" (Asset,     debit-normal)
_ACCT_4100 = _lId("4100")   # sales revenue  — cFunc "R" (Revenue,   credit-normal)
_ACCT_5100 = _lId("5100")   # salaries       — cFunc "X" (Expense,   debit-normal)
_ACCT_2100 = _lId("2100")   # HST payable    — cFunc "M" (Liability, credit-normal)

_COA_FUNCS = {
    _ACCT_1060: "A",
    _ACCT_4100: "R",
    _ACCT_5100: "X",
    _ACCT_2100: "M",
}


# ---------------------------------------------------------------------------
# Check registry
# ---------------------------------------------------------------------------

_checks: list[tuple[str, bool]] = []


def _check(label: str, value: bool) -> None:
    _checks.append((label, value))
    print(f"  [{'PASS' if value else 'FAIL'}] {label}")


# ---------------------------------------------------------------------------
# Import functions under test
# ---------------------------------------------------------------------------

from sage50.bridge_reader import (
    _map_gl,
    decode_dr_cr,
    fetch_tax_summary,
    fetch_trial_balance,
)


# ===========================================================================
# Suite 0 — decode_dr_cr unit test (the central decoder)
# ===========================================================================

print("\n--- Suite 0: decode_dr_cr ---")

_check("0a debit-normal  (A) +500 → debit=500,  credit=0",
       decode_dr_cr(_ACCT_1060, Decimal("500"),  _COA_FUNCS) == (Decimal("500"), Decimal("0")))
_check("0b debit-normal  (A) -200 → debit=0,    credit=200",
       decode_dr_cr(_ACCT_1060, Decimal("-200"), _COA_FUNCS) == (Decimal("0"),   Decimal("200")))
_check("0c credit-normal (R) +1000 → debit=0,   credit=1000",
       decode_dr_cr(_ACCT_4100, Decimal("1000"), _COA_FUNCS) == (Decimal("0"),   Decimal("1000")))
_check("0d credit-normal (R) -300 → debit=300,  credit=0",
       decode_dr_cr(_ACCT_4100, Decimal("-300"), _COA_FUNCS) == (Decimal("300"), Decimal("0")))
_check("0e credit-normal (M) +800 → debit=0,    credit=800",
       decode_dr_cr(_ACCT_2100, Decimal("800"),  _COA_FUNCS) == (Decimal("0"),   Decimal("800")))
_check("0f unknown lAcctId → falls back to debit-normal",
       decode_dr_cr("99999999", Decimal("100"),  _COA_FUNCS) == (Decimal("100"), Decimal("0")))


# ===========================================================================
# Suite 1 — _map_gl: with coa_funcs (M1.2 corrected behaviour)
# ===========================================================================

print("\n--- Suite 1: _map_gl sign decode ---")

# 1a — debit-normal asset 1060, positive → debit
_r1a = _map_gl(_gl_row(_ACCT_1060, "500.00"), _COA_FUNCS)
_check("1a asset  +500 → debit=500",    _r1a.debit  == Decimal("500.00"))
_check("1a asset  +500 → credit=0",     _r1a.credit == Decimal("0"))

# 1b — debit-normal asset 1060, negative → credit
_r1b = _map_gl(_gl_row(_ACCT_1060, "-200.00"), _COA_FUNCS)
_check("1b asset  -200 → debit=0",      _r1b.debit  == Decimal("0"))
_check("1b asset  -200 → credit=200",   _r1b.credit == Decimal("200.00"))

# 1c — debit-normal expense 5100, positive → debit
_r1c = _map_gl(_gl_row(_ACCT_5100, "300.00"), _COA_FUNCS)
_check("1c expense +300 → debit=300",   _r1c.debit  == Decimal("300.00"))
_check("1c expense +300 → credit=0",    _r1c.credit == Decimal("0"))

# 1d — credit-normal revenue 4100, positive dAmount = CREDIT
_r1d = _map_gl(_gl_row(_ACCT_4100, "1000.00"), _COA_FUNCS)
_check("1d revenue +1000 → debit=0    (credit-normal; positive = credit)",
       _r1d.debit  == Decimal("0"))
_check("1d revenue +1000 → credit=1000",
       _r1d.credit == Decimal("1000.00"))

# 1e — credit-normal liability 2100, positive dAmount = CREDIT
_r1e = _map_gl(_gl_row(_ACCT_2100, "800.00"), _COA_FUNCS)
_check("1e liability +800 → debit=0   (credit-normal; positive = credit)",
       _r1e.debit  == Decimal("0"))
_check("1e liability +800 → credit=800",
       _r1e.credit == Decimal("800.00"))

# 1f — fallback: _map_gl without coa_funcs → debit-normal for all
_r1f = _map_gl(_gl_row(_ACCT_4100, "1000.00"), None)
_check("1f revenue +1000 without coa_funcs → debit=1000 (debit-normal fallback)",
       _r1f.debit  == Decimal("1000.00"))


# ===========================================================================
# Suite 2 — fetch_trial_balance
# ===========================================================================

print("\n--- Suite 2: fetch_trial_balance sign decode ---")

_TB_COA = [
    _coa_row(_ACCT_1060, "A", "Bank - TD Chequing"),
    _coa_row(_ACCT_4100, "R", "Sales Revenue"),
    _coa_row(_ACCT_5100, "X", "Salaries Expense"),
    _coa_row(_ACCT_2100, "M", "HST Payable"),
]
_TB_GL = [
    # Bank 1060: net +3000 → debit balance (debit-normal asset)
    _gl_row(_ACCT_1060, "1000.00"),
    _gl_row(_ACCT_1060, "1500.00"),
    _gl_row(_ACCT_1060,  "500.00"),
    # Revenue 4100: net +2500 → credit balance (credit-normal revenue)
    _gl_row(_ACCT_4100, "2000.00"),
    _gl_row(_ACCT_4100,  "500.00"),
    # Expense 5100: net +400 → debit balance (debit-normal expense)
    _gl_row(_ACCT_5100,  "400.00"),
    # HST payable 2100: net +600 → credit balance (credit-normal liability)
    _gl_row(_ACCT_2100,  "600.00"),
]

_tb_returns = iter([_TB_COA, _TB_GL])

with patch("sage50.bridge_reader._get_creds", return_value=("dummy.sai", "u", "p")), \
     patch("sage50.bridge_reader._run_bridge", side_effect=lambda *a, **kw: next(_tb_returns)):
    _tb = {l.account_no: l for l in fetch_trial_balance()}

# 2a — asset 1060: positive net → debit balance
_check("2a bank  1060 debit=3000",    "1060" in _tb and _tb["1060"].debit  == Decimal("3000.00"))
_check("2a bank  1060 credit=0",      "1060" in _tb and _tb["1060"].credit == Decimal("0"))

# 2b — expense 5100: positive net → debit balance
_check("2b expense 5100 debit=400",   "5100" in _tb and _tb["5100"].debit  == Decimal("400.00"))
_check("2b expense 5100 credit=0",    "5100" in _tb and _tb["5100"].credit == Decimal("0"))

# 2c — revenue 4100: positive net → CREDIT balance
_check("2c revenue 4100 debit=0",     "4100" in _tb and _tb["4100"].debit  == Decimal("0"))
_check("2c revenue 4100 credit=2500", "4100" in _tb and _tb["4100"].credit == Decimal("2500.00"))

# 2d — liability 2100: positive net → CREDIT balance
_check("2d HST payable 2100 debit=0",   "2100" in _tb and _tb["2100"].debit  == Decimal("0"))
_check("2d HST payable 2100 credit=600","2100" in _tb and _tb["2100"].credit == Decimal("600.00"))


# ===========================================================================
# Suite 3 — fetch_tax_summary
# ===========================================================================

print("\n--- Suite 3: fetch_tax_summary sign decode ---")

_TS_COA = [
    _coa_row(_ACCT_4100, "R", "Sales Revenue"),
    _coa_row(_ACCT_5100, "X", "Advertising Expense"),
]
_TS_GL = [
    # Revenue 4100: 2 sales credits → positive dAmount (balance-impact = credit)
    _gl_row(_ACCT_4100, "3000.00"),
    _gl_row(_ACCT_4100, "2000.00"),
    # Expense 5100: 1 purchase → positive dAmount (debit-normal = debit = purchase)
    _gl_row(_ACCT_5100,  "800.00"),
]

_ts_returns = iter([_TS_COA, _TS_GL])

with patch("sage50.bridge_reader._get_creds", return_value=("dummy.sai", "u", "p")), \
     patch("sage50.bridge_reader._run_bridge", side_effect=lambda *a, **kw: next(_ts_returns)):
    _ts = fetch_tax_summary(
        date(2025, 12, 1), date(2025, 12, 31),
        tax_code="H", tax_rate="0.13",
    )

_check("3a taxable_sales     == 5000",   _ts["taxable_sales"]     == Decimal("5000.00"))
_check("3a tax_collected      == 650.00", _ts["tax_collected"]     == Decimal("650.00"))
_check("3b taxable_purchases  == 800",    _ts["taxable_purchases"] == Decimal("800.00"))
_check("3b itc_claimed        == 104.00", _ts["itc_claimed"]       == Decimal("104.00"))
_check("3c net_tax            == 546.00", _ts["net_tax"]           == Decimal("546.00"))


# ===========================================================================
# Results
# ===========================================================================

n_pass = sum(1 for _, v in _checks if v)
n_fail = sum(1 for _, v in _checks if not v)
total  = len(_checks)

print(f"\n{'=' * 60}")
print("bridge_sign_smoke — M1.2 balance-impact sign decode")
print(f"{'=' * 60}")
print(f"  {n_pass}/{total} checks passed")

if n_fail:
    print("\nFailed checks:")
    for lbl, v in _checks:
        if not v:
            print(f"  FAIL: {lbl}")
    sys.exit(1)

print("\nAll checks passed.")
