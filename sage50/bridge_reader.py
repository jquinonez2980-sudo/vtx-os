"""
Sage50Bridge reader — calls Sage50Bridge.exe as a subprocess and maps raw
DB rows to Pydantic model instances.

Sage50Bridge.exe must be compiled (run build.ps1 in sage50_bridge/) before use.
Sage 50 must be CLOSED when this module runs — the SDK opens the .SAI file
exclusively. If Sage 50 is open, OpenDatabase fails with FAIL_MYSQL_NOTRUNNING.

Credential resolution order for each parameter:
  1. Explicit kwarg passed by caller (sai_file / sage50_user / sage50_password)
  2. Environment variable  VTX_SAGE50_SAI / VTX_SAGE50_USER / VTX_SAGE50_PASSWORD
  3. Secret Manager secrets: vtx-sage50-company-path / vtx-sage50-password
     (user defaults to "sysadmin" if not found)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from models.sage50 import (
    APBill,
    ARInvoice,
    ChartOfAccountsEntry,
    Customer,
    GLTransaction,
    Vendor,
)

_BRIDGE_EXE = Path(__file__).parent.parent / "sage50_bridge" / "Sage50Bridge.exe"

# ---------------------------------------------------------------------------
# Credential resolution
# ---------------------------------------------------------------------------

def _resolve(explicit: str | None, env_var: str, secret_name: str, default: str | None = None) -> str | None:
    if explicit:
        return explicit
    val = os.environ.get(env_var)
    if val:
        return val
    try:
        from core.secrets import get
        return get(secret_name)
    except Exception as exc:
        if default is not None:
            return default
        raise RuntimeError(f"Cannot resolve '{secret_name}' from Secret Manager: {exc}") from exc


def _get_creds(sai_file: str | None, user: str | None, password: str | None) -> tuple[str, str, str]:
    resolved_sai  = _resolve(sai_file,  "VTX_SAGE50_SAI",      "vtx-sage50-company-path")
    resolved_user = _resolve(user,      "VTX_SAGE50_USER",     "vtx-sage50-user",     "sysadmin")
    resolved_pass = _resolve(password,  "VTX_SAGE50_PASSWORD", "vtx-sage50-password", "")
    if not resolved_sai:
        raise ValueError(
            "Sage 50 SAI file path not provided. Pass sai_file=, set env var "
            "VTX_SAGE50_SAI, or store in Secret Manager as vtx-sage50-company-path."
        )
    return resolved_sai, resolved_user or "sysadmin", resolved_pass or ""


# ---------------------------------------------------------------------------
# Bridge subprocess caller
# ---------------------------------------------------------------------------

def _run_bridge(
    sai_file: str,
    user: str,
    password: str,
    table: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[dict]:
    if not _BRIDGE_EXE.exists():
        raise FileNotFoundError(
            f"Sage50Bridge.exe not found at {_BRIDGE_EXE}. "
            "Run build.ps1 in the sage50_bridge/ directory first."
        )

    cmd = [
        str(_BRIDGE_EXE),
        "--sai", sai_file,
        "--user", user,
        "--password", password,
        "--table", table,
    ]
    if start_date:
        cmd += ["--start-date", start_date.isoformat()]
    if end_date:
        cmd += ["--end-date", end_date.isoformat()]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError(
            f"Sage50Bridge produced no output for table='{table}'. "
            f"stderr: {result.stderr.strip()}"
        )

    data = json.loads(stdout)
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(f"Sage50Bridge error [{table}]: {data['error']}")
    if not isinstance(data, list):
        raise RuntimeError(f"Sage50Bridge returned unexpected type {type(data)} for table='{table}'")
    return data


# ---------------------------------------------------------------------------
# Type helpers
# ---------------------------------------------------------------------------

def _str(v: Any) -> str:
    return "" if v is None else str(v).strip()

def _str_or_none(v: Any) -> str | None:
    s = "" if v is None else str(v).strip()
    return s if s else None

def _dec(v: Any) -> Decimal:
    if v is None:
        return Decimal("0")
    s = str(v).replace("$", "").replace(",", "").strip()
    if not s:
        return Decimal("0")
    try:
        return Decimal(s)
    except Exception:
        return Decimal("0")

def _active(v: Any) -> bool:
    # bInactive=1 means inactive; bInactive=0 means active
    return not bool(v)

_COA_TYPE: dict[str, str] = {
    "A": "Asset",  "B": "Asset",  "C": "Asset",
    "L": "Liability", "M": "Liability",
    "E": "Equity", "F": "Equity",
    "R": "Revenue", "I": "Revenue", "O": "Revenue",
    "X": "Expense", "Y": "Expense",
}


# ---------------------------------------------------------------------------
# Chart of Accounts
# DB table: taccount  (lId, sName, cFunc, nAcctClass, sGifiCode, dYts, bInactive)
# ---------------------------------------------------------------------------

def fetch_chart_of_accounts(
    *,
    sai_file: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> list[ChartOfAccountsEntry]:
    sai, usr, pwd = _get_creds(sai_file, user, password)
    rows = _run_bridge(sai, usr, pwd, "coa")
    return [_map_coa(r) for r in rows]


def _map_coa(r: dict) -> ChartOfAccountsEntry:
    func = _str_or_none(r.get("cFunc")) or ""
    return ChartOfAccountsEntry.model_validate({
        "Account No.":        _str(r.get("lId")),
        "Account Description": _str(r.get("sName")),
        "Account Type":       _COA_TYPE.get(func.upper(), func),
        "Account Class":      _str_or_none(r.get("nAcctClass")),
        "GIFI Code":          _str_or_none(r.get("sGifiCode")),
        "Balance":            _dec(r.get("dYts")),
        "Active":             _active(r.get("bInactive", 0)),
    })


# ---------------------------------------------------------------------------
# Customers
# DB table: tcustomr  (lId, sName, sCntcName, sStreet1, sStreet2, sCity,
#                      sProvState, sPostalZip, sPhone1, sEmail,
#                      dCrLimit, dAmtYtd, bInactive, lTaxCode)
# ---------------------------------------------------------------------------

def fetch_customers(
    *,
    sai_file: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> list[Customer]:
    sai, usr, pwd = _get_creds(sai_file, user, password)
    rows = _run_bridge(sai, usr, pwd, "customers")
    return [_map_customer(r) for r in rows]


def _map_customer(r: dict) -> Customer:
    return Customer.model_validate({
        "Customer No.":  _str(r.get("lId")),
        "Company Name":  _str_or_none(r.get("sName")),
        "Contact Name":  _str(r.get("sCntcName") or r.get("sName") or ""),
        "Address 1":     _str_or_none(r.get("sStreet1")),
        "Address 2":     _str_or_none(r.get("sStreet2")),
        "City":          _str_or_none(r.get("sCity")),
        "Province":      _str_or_none(r.get("sProvState")),
        "Postal Code":   _str_or_none(r.get("sPostalZip")),
        "Phone 1":       _str_or_none(r.get("sPhone1")),
        "E-mail":        _str_or_none(r.get("sEmail")),
        "Credit Limit":  _dec(r.get("dCrLimit")),
        "Balance":       _dec(r.get("dAmtYtd")),
        "Active":        _active(r.get("bInactive", 0)),
        # lTaxCode is an integer FK — omit; no string tax code in DB
    })


# ---------------------------------------------------------------------------
# Vendors
# DB table: tvendor  (lId, sName, sCntcName, sStreet1, sStreet2, sCity,
#                     sProvState, sPostalZip, sPhone1, sEmail,
#                     sTaxId, dAmtYtd, bInactive)
# ---------------------------------------------------------------------------

def fetch_vendors(
    *,
    sai_file: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> list[Vendor]:
    sai, usr, pwd = _get_creds(sai_file, user, password)
    rows = _run_bridge(sai, usr, pwd, "vendors")
    return [_map_vendor(r) for r in rows]


def _map_vendor(r: dict) -> Vendor:
    return Vendor.model_validate({
        "Vendor No.":    _str(r.get("lId")),
        "Company Name":  _str_or_none(r.get("sName")),
        "Contact Name":  _str(r.get("sCntcName") or r.get("sName") or ""),
        "Address 1":     _str_or_none(r.get("sStreet1")),
        "Address 2":     _str_or_none(r.get("sStreet2")),
        "City":          _str_or_none(r.get("sCity")),
        "Province":      _str_or_none(r.get("sProvState")),
        "Postal Code":   _str_or_none(r.get("sPostalZip")),
        "Phone 1":       _str_or_none(r.get("sPhone1")),
        "E-mail":        _str_or_none(r.get("sEmail")),
        "Balance":       _dec(r.get("dAmtYtd")),
        "Business No.":  _str_or_none(r.get("sTaxId")),
        "Active":        _active(r.get("bInactive", 0)),
    })


# ---------------------------------------------------------------------------
# GL Transactions
# Bridge query: UNION of current-year (tjourent+tjentact) and archived-year
# (tjeh0N+tjeah0N) journals, filtered on dtJourDate — the accounting date, not
# dtASDate the data-entry timestamp (see Program.cs ExportGl).
# Result columns: lJEntID, txnDate, sSource, hdrComment, nLineNum, lAcctId,
#                 dAmount, szComment
# Fallback result (union fails): current-year only, then tjentact flat (no txnDate)
#
# dAmount sign convention: positive = debit, negative = credit
# ---------------------------------------------------------------------------

def fetch_gl_transactions(
    start_date: date | None = None,
    end_date: date | None = None,
    *,
    sai_file: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> list[GLTransaction]:
    sai, usr, pwd = _get_creds(sai_file, user, password)
    rows = _run_bridge(sai, usr, pwd, "gl", start_date, end_date)

    # If we requested a date range and got nothing, Sage 50 may have stored the entries
    # with fiscal-adjusted dates outside the range (e.g. "date precedes Fiscal Start"
    # causes entries to land on the prior fiscal year-end date).  Retry without date
    # filtering so the caller at least sees something; fetch_gl_csv() writes the actual
    # stored dates so the reconciliation agent can still try to match them.
    if not rows and (start_date or end_date):
        import sys
        print(
            f"[fetch_gl_transactions] date-filtered query returned 0 rows for "
            f"{start_date}–{end_date}; retrying without date filter "
            f"(entries may have fiscal-adjusted dates — check Sage 50 fiscal year start)",
            file=sys.stderr,
        )
        rows = _run_bridge(sai, usr, pwd, "gl", None, None)

    return [_map_gl(r) for r in rows]


def _map_gl(r: dict) -> GLTransaction:
    amount = _dec(r.get("dAmount"))
    debit  = amount if amount >= 0 else Decimal("0")
    credit = -amount if amount < 0 else Decimal("0")
    # szComment = line-level note (often empty); hdrComment = entry-level description
    description = _str(r.get("szComment") or r.get("hdrComment") or "")
    return GLTransaction.model_validate({
        "Date":                r.get("txnDate"),
        "Journal No.":         _str(r.get("lJEntID")),
        "Source":              _str(r.get("sSource") or ""),
        "Account No.":         _str(r.get("lAcctId")),
        "Account Description": _str(r.get("acctName") or ""),
        "Debit":               debit,
        "Credit":              credit,
        "Comment":             description,
    })


def fetch_gl_csv(
    period: str,
    dest_path: str | Path,
    *,
    account_map: dict[str, str] | None = None,
    load_bq: bool = True,
    sai_file: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> int:
    """Fetch GL for *period* from Sage 50 and write a Sage 50 export-format CSV.

    The "Account No." column uses display codes (e.g. "1060") when *account_map*
    is supplied (4-digit → 8-digit lId), so parse_gl_csv() can filter by
    gl_bank_account.  Rows not in the map keep their raw lId value.

    Args:
        period:      "YYYY-MM"
        dest_path:   file path to write; parent directory must exist
        account_map: 4-digit code → 8-digit lId mapping for reverse translation
        load_bq:     also stream rows into vtx_accounting.gl_transactions (non-fatal)
        sai_file, user, password: bridge credentials; fall back to Secret Manager

    Returns the number of GL lines written.
    """
    import calendar as _cal
    import csv as _csv
    from datetime import date as _date
    from pathlib import Path as _Path

    year, month = int(period[:4]), int(period[5:7])
    last_day = _cal.monthrange(year, month)[1]

    gl_txns = fetch_gl_transactions(
        start_date=_date(year, month, 1),
        end_date=_date(year, month, last_day),
        sai_file=sai_file,
        user=user,
        password=password,
    )

    reverse_map: dict[str, str] = (
        {lid: code for code, lid in account_map.items()} if account_map else {}
    )

    with open(_Path(dest_path), "w", newline="", encoding="utf-8-sig") as fh:
        writer = _csv.DictWriter(fh, fieldnames=[
            "Date", "Source No.", "Account No.", "Account Description",
            "Debit", "Credit", "Description",
        ])
        writer.writeheader()
        for txn in gl_txns:
            acct     = reverse_map.get(txn.account_no, txn.account_no)
            date_str = txn.transaction_date.strftime("%m/%d/%Y") if txn.transaction_date else ""
            writer.writerow({
                "Date":                date_str,
                "Source No.":          txn.journal_no,
                "Account No.":         acct,
                "Account Description": txn.account_name,
                "Debit":               str(txn.debit)  if txn.debit  else "",
                "Credit":              str(txn.credit) if txn.credit else "",
                "Description":         txn.description,
            })

    if load_bq:
        try:
            from core.bq_loader import ensure_table, load_rows
            from models.sage50 import GLTransaction
            ensure_table("vtx_accounting", "gl_transactions", GLTransaction,
                         partition_field="transaction_date", cluster_fields=["account_no"])
            load_rows("vtx_accounting", "gl_transactions", gl_txns)
        except Exception as exc:
            import sys
            print(f"[fetch_gl_csv] BQ load skipped (non-fatal): {exc}", file=sys.stderr)

    return len(gl_txns)


# ---------------------------------------------------------------------------
# AR Invoices
# Bridge query: trcsal JOIN trcsall (see Program.cs ExportAr)
# Result columns: invoiceId, txnDate, lCusId, nLineNum, dAmount, dPrice,
#                 dQuantity, lAcctId, dTaxAmt, sDesc
# ---------------------------------------------------------------------------

def fetch_ar_invoices(
    start_date: date | None = None,
    end_date: date | None = None,
    *,
    sai_file: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> list[ARInvoice]:
    sai, usr, pwd = _get_creds(sai_file, user, password)
    rows = _run_bridge(sai, usr, pwd, "ar", start_date, end_date)
    return [_map_ar(r) for r in rows]


def _map_ar(r: dict) -> ARInvoice:
    amount    = _dec(r.get("dAmount"))
    tax_amt   = _dec(r.get("dTaxAmt"))
    return ARInvoice.model_validate({
        "Invoice No.":          _str(r.get("invoiceId")),
        "Date":                 r.get("txnDate"),
        "Customer No.":         _str(r.get("lCusId")),
        "Customer Name":        "",
        "Amount":               amount,
        "Tax Amount":           tax_amt,
        "Amount Including Tax": amount + tax_amt,
        "Paid":                 False,
    })


# ---------------------------------------------------------------------------
# AP Bills
# Bridge query: trcpur JOIN trcpurl (see Program.cs ExportAp)
# Result columns: invoiceId, txnDate, lVenId, nLineNum, dAmount, dPrice,
#                 dQuantity, lAcctId, dTaxAmt, sDesc
# ---------------------------------------------------------------------------

def fetch_ap_bills(
    start_date: date | None = None,
    end_date: date | None = None,
    *,
    sai_file: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> list[APBill]:
    sai, usr, pwd = _get_creds(sai_file, user, password)
    rows = _run_bridge(sai, usr, pwd, "ap", start_date, end_date)
    return [_map_ap(r) for r in rows]


def _map_ap(r: dict) -> APBill:
    amount  = _dec(r.get("dAmount"))
    tax_amt = _dec(r.get("dTaxAmt"))
    return APBill.model_validate({
        "Invoice No.":          _str(r.get("invoiceId")),
        "Date":                 r.get("txnDate"),
        "Vendor No.":           _str(r.get("lVenId")),
        "Vendor Name":          "",
        "Amount":               amount,
        "Tax Amount":           tax_amt,
        "Amount Including Tax": amount + tax_amt,
        "Paid":                 False,
    })


# ---------------------------------------------------------------------------
# Trial Balance
# Computed from all accumulated GL entries through period_end.
# Sage 50 stores account IDs as 8-digit integers (e.g. 11000000 = account 1100).
# _lId_to_display() extracts the 4-digit user-visible code by integer division.
# ---------------------------------------------------------------------------

_NUMERIC_DISPLAY_RE = re.compile(r"^\d{3,6}$")

_REVENUE_FUNCS  = frozenset("RIO")
_EXPENSE_FUNCS  = frozenset("XY")
_POSTING_FUNCS  = frozenset("ABCLMEFRIOXY")


def _lId_to_display(lid: str) -> str:
    """Convert 8-digit Sage 50 lId to 4-digit display code: '11000000' → '1100'."""
    try:
        n = int(lid)
        if n >= 10000:
            return str(n // 10000)
        return lid
    except (ValueError, TypeError):
        return lid


def fetch_trial_balance(
    period_end: date | None = None,
    *,
    sai_file: str | None = None,
    user: str | None = None,
    password: str | None = None,
):
    """Compute trial balance as at period_end by aggregating all GL entries.

    Returns list[TBLine] sorted by account code.  Positive net dAmount = debit
    balance; negative = credit balance (Sage 50 convention).

    If period_end is None, all GL entries are included (full history).
    """
    from collections import defaultdict
    from sage50.trial_balance_parser import TBLine

    sai, usr, pwd = _get_creds(sai_file, user, password)

    coa_rows = _run_bridge(sai, usr, pwd, "coa")
    coa_map: dict[str, tuple[str, str, str]] = {}
    for r in coa_rows:
        lid  = _str(r.get("lId"))
        func = (_str_or_none(r.get("cFunc")) or "").upper()
        if not lid or func not in _POSTING_FUNCS:
            continue
        display = _lId_to_display(lid)
        coa_map[lid] = (display, _str(r.get("sName")), func)

    gl_rows = _run_bridge(sai, usr, pwd, "gl", None, period_end)

    net_by_acct: dict[str, Decimal] = defaultdict(Decimal)
    for r in gl_rows:
        lid = _str(r.get("lAcctId"))
        if lid in coa_map:
            net_by_acct[lid] += _dec(r.get("dAmount"))

    lines = []
    for lid, net in net_by_acct.items():
        if net == Decimal("0"):
            continue
        display, name, _ = coa_map[lid]
        if not _NUMERIC_DISPLAY_RE.match(display):
            continue
        if net > 0:
            debit, credit = net, Decimal("0")
        else:
            debit, credit = Decimal("0"), abs(net)
        lines.append(TBLine(account_no=display, description=name, debit=debit, credit=credit))

    lines.sort(key=lambda l: l.account_no)
    return lines


def fetch_trial_balance_csv(
    period: str,
    dest_path: "str | Path",
    *,
    sai_file: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> int:
    """Write trial balance CSV for period to dest_path.

    Output format matches Sage 50 export and trial_balance_parser.py:
        Account Number, Account Description, Debits, Credits

    Returns the number of account lines written.
    """
    import calendar as _cal
    import csv as _csv
    from pathlib import Path as _Path

    year, month  = int(period[:4]), int(period[5:7])
    last_day     = _cal.monthrange(year, month)[1]
    period_end_d = date(year, month, last_day)

    lines = fetch_trial_balance(period_end_d, sai_file=sai_file, user=user, password=password)

    dest = _Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", newline="", encoding="utf-8-sig") as fh:
        writer = _csv.DictWriter(fh, fieldnames=[
            "Account Number", "Account Description", "Debits", "Credits",
        ])
        writer.writeheader()
        for line in lines:
            writer.writerow({
                "Account Number":      line.account_no,
                "Account Description": line.description,
                "Debits":              str(line.debit)  if line.debit  else "",
                "Credits":             str(line.credit) if line.credit else "",
            })

    return len(lines)


# ---------------------------------------------------------------------------
# Tax Summary (derived from GL — period activity on revenue + expense accounts)
# Output format matches PrepareHSTReturnAgent._read_tax_csv():
#   Period Start, Period End, Tax Code, Description,
#   Taxable Sales, Tax Collected, Taxable Purchases, Input Tax Credits, Net Tax
# ---------------------------------------------------------------------------

def fetch_tax_summary(
    start_date: date | None = None,
    end_date: date | None = None,
    *,
    tax_code: str = "H",
    tax_rate: "Decimal | str | None" = None,
    sai_file: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> dict:
    """Compute HST/GST summary for the date range from GL + COA.

    Revenue credits (cFunc R/I/O) → taxable_sales.
    Expense debits  (cFunc X/Y)   → taxable_purchases.
    tax_rate defaults to 0.13 for Ontario HST (tax_code='H').

    Returns a dict with keys: taxable_sales, tax_collected, taxable_purchases,
    itc_claimed, net_tax, tax_code.
    """
    _TAX_RATES = {"H": Decimal("0.13"), "HST": Decimal("0.13"),
                  "G": Decimal("0.05"), "GST": Decimal("0.05"),
                  "Q": Decimal("0.09975"), "QST": Decimal("0.09975")}
    rate = (
        Decimal(str(tax_rate))
        if tax_rate is not None
        else _TAX_RATES.get(tax_code.upper(), Decimal("0.13"))
    )

    sai, usr, pwd = _get_creds(sai_file, user, password)

    coa_rows = _run_bridge(sai, usr, pwd, "coa")
    revenue_ids: set[str] = set()
    expense_ids: set[str] = set()
    for r in coa_rows:
        lid  = _str(r.get("lId"))
        func = (_str_or_none(r.get("cFunc")) or "").upper()
        if func in _REVENUE_FUNCS:
            revenue_ids.add(lid)
        elif func in _EXPENSE_FUNCS:
            expense_ids.add(lid)

    gl_rows = _run_bridge(sai, usr, pwd, "gl", start_date, end_date)

    taxable_sales = Decimal("0")
    taxable_purchases = Decimal("0")
    for r in gl_rows:
        lid    = _str(r.get("lAcctId"))
        amount = _dec(r.get("dAmount"))
        if lid in revenue_ids and amount < 0:
            taxable_sales += abs(amount)
        elif lid in expense_ids and amount > 0:
            taxable_purchases += amount

    tax_collected = (taxable_sales     * rate).quantize(Decimal("0.01"))
    itc_claimed   = (taxable_purchases * rate).quantize(Decimal("0.01"))
    net_tax       = tax_collected - itc_claimed

    return {
        "taxable_sales":     taxable_sales.quantize(Decimal("0.01")),
        "tax_collected":     tax_collected,
        "taxable_purchases": taxable_purchases.quantize(Decimal("0.01")),
        "itc_claimed":       itc_claimed,
        "net_tax":           net_tax,
        "tax_code":          tax_code,
    }


def fetch_tax_summary_csv(
    period: str,
    dest_path: "str | Path",
    *,
    tax_code: str = "H",
    tax_rate: "Decimal | str | None" = None,
    sai_file: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> dict:
    """Write Tax Summary CSV for period to dest_path.

    Output matches PrepareHSTReturnAgent._read_tax_csv() format.
    Returns the summary dict (same as fetch_tax_summary()).
    """
    import calendar as _cal
    import csv as _csv
    from pathlib import Path as _Path

    _TAX_DESC = {"H": "Ontario HST (13%)", "HST": "Ontario HST (13%)",
                 "G":  "GST (5%)",         "GST":  "GST (5%)"}

    year, month  = int(period[:4]), int(period[5:7])
    last_day     = _cal.monthrange(year, month)[1]
    start_d      = date(year, month, 1)
    end_d        = date(year, month, last_day)

    summary = fetch_tax_summary(
        start_d, end_d,
        tax_code=tax_code, tax_rate=tax_rate,
        sai_file=sai_file, user=user, password=password,
    )

    dest = _Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        writer = _csv.writer(fh)
        writer.writerow([
            "Period Start", "Period End", "Tax Code", "Description",
            "Taxable Sales", "Tax Collected", "Taxable Purchases",
            "Input Tax Credits", "Net Tax",
        ])
        writer.writerow([
            start_d.strftime("%m/%d/%Y"),
            end_d.strftime("%m/%d/%Y"),
            summary["tax_code"],
            _TAX_DESC.get(summary["tax_code"].upper(), f"HST ({summary['tax_code']})"),
            str(summary["taxable_sales"]),
            str(summary["tax_collected"]),
            str(summary["taxable_purchases"]),
            str(summary["itc_claimed"]),
            str(summary["net_tax"]),
        ])

    return summary


# ---------------------------------------------------------------------------
# Unsupported report types
# ---------------------------------------------------------------------------

def fetch_inventory(**_: Any) -> list:
    raise NotImplementedError("inventory: not available via Sage50Bridge (no direct DB table mapping)")

def fetch_payroll(**_: Any) -> list:
    raise NotImplementedError("payroll: not available via Sage50Bridge (no direct DB table mapping)")

def fetch_bank_reconciliation(**_: Any) -> list:
    raise NotImplementedError("bank_reconciliation: not available via Sage50Bridge (no direct DB table mapping)")


# ---------------------------------------------------------------------------
# Write: post general journal entries
# ---------------------------------------------------------------------------

def post_journal_entries(
    entries: list[dict],
    *,
    sai_file: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> dict:
    """Post a list of double-entry journal entries into Sage 50's General Journal.

    Each entry dict must have:
        date      str  "YYYY-MM-DD"
        source    str  transaction source code, max 12 chars (e.g. "BNK")
        comment   str  header comment, max 39 chars
        lines     list of {account_id, debit, credit, comment}

    Every entry must be balanced: sum(debit) == sum(credit).
    Sage 50 must be CLOSED before calling — the bridge opens the .SAI exclusively.

    Returns the raw bridge result dict:
        posted  int   — entries successfully posted
        total   int   — entries attempted
        errors  int   — entries that failed
        results list  — per-entry {date, comment, posted, journal_no | error}
    """
    if not _BRIDGE_EXE.exists():
        raise FileNotFoundError(
            f"Sage50Bridge.exe not found at {_BRIDGE_EXE}. "
            "Run build.ps1 in the sage50_bridge/ directory first."
        )

    sai, usr, pwd = _get_creds(sai_file, user, password)

    cmd = [
        str(_BRIDGE_EXE),
        "--sai",      sai,
        "--user",     usr,
        "--password", pwd,
        "--mode",     "write",
    ]

    json_input = json.dumps(entries, default=str)
    result = subprocess.run(
        cmd,
        input=json_input,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    stderr = result.stderr.strip()
    if stderr:
        import sys
        print(stderr, file=sys.stderr)

    if result.returncode != 0:
        raise RuntimeError(
            f"Sage50Bridge write failed (exit {result.returncode}): {stderr}"
        )

    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError("Sage50Bridge write mode returned no output")

    data = json.loads(stdout)
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(f"Sage50Bridge write error: {data['error']}")

    import sys
    total   = data.get("total",   0)
    posted  = data.get("posted",  0)
    errors  = data.get("errors",  0)
    print(
        f"[post_journal_entries] total={total}  posted={posted}  errors={errors}",
        file=sys.stderr,
    )
    for i, r in enumerate(data.get("results", []), 1):
        date_str    = r.get("date", "")
        comment     = (r.get("comment") or "")[:40]
        if r.get("posted"):
            print(
                f"  [{i:>3}] OK    journal_no={r.get('journal_no')}  {date_str}  {comment}",
                file=sys.stderr,
            )
        else:
            print(
                f"  [{i:>3}] FAIL  {date_str}  {comment}  error={r.get('error', '')}",
                file=sys.stderr,
            )

    return data
