"""
Offline smoke test for the Gmail watcher pipeline.
Tests imports, CLI flag parsing, and the OCR parser with synthetic text.
Does NOT call Gmail API, Document AI, GCS, or BookkeepingAgent.
"""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_checks: list[tuple[str, bool, str]] = []

def check(label: str, value: bool, detail: str = "") -> None:
    _checks.append((label, value, detail))
    status = "PASS" if value else "FAIL"
    suffix = f"  ({detail})" if detail and not value else ""
    print(f"  [{status}] {label}{suffix}")


# ---------------------------------------------------------------------------
# 1. Imports
# ---------------------------------------------------------------------------
print("\n-- Imports --")
try:
    from core.gmail_notifier import GmailNotifier, _safe_filename, _walk_parts
    check("core.gmail_notifier imports", True)
except Exception as e:
    check("core.gmail_notifier imports", False, str(e))

try:
    from core.docai_ocr import DocAIOCR, ocr_pdf_bytes, ocr_pdf_file
    check("core.docai_ocr imports", True)
except Exception as e:
    check("core.docai_ocr imports", False, str(e))

try:
    from sage50.bank_statement_ocr_parser import (
        detect_bank, parse_ocr_text, write_csv, parse_and_write_csv,
    )
    check("sage50.bank_statement_ocr_parser imports", True)
except Exception as e:
    check("sage50.bank_statement_ocr_parser imports", False, str(e))

try:
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "gmail_watcher", Path(__file__).parent / "gmail_watcher.py"
    )
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)  # type: ignore
    check("scripts.gmail_watcher imports", True)
except Exception as e:
    check("scripts.gmail_watcher imports", False, str(e))


# ---------------------------------------------------------------------------
# 2. GmailNotifier -- instantiation (no API calls)
# ---------------------------------------------------------------------------
print("\n-- GmailNotifier (no API) --")
try:
    _n = GmailNotifier()
    check("GmailNotifier() instantiates", True)
    check("GmailNotifier._service is None on init", _n._service is None)
except Exception as e:
    check("GmailNotifier instantiation", False, str(e))

check("_safe_filename clean",   _safe_filename("statement.pdf") == "statement.pdf")
check("_safe_filename spaces",  _safe_filename("my bank.pdf")   == "my_bank.pdf")
check("_safe_filename empty",   bool(_safe_filename("")))

_payload = {
    "mimeType": "multipart/mixed",
    "parts": [
        {"mimeType": "text/plain",  "filename": "", "body": {"data": "abc"}},
        {
            "mimeType": "application/pdf",
            "filename": "statement.pdf",
            "body": {"attachmentId": "att123", "size": 50000},
        },
        {
            "mimeType": "multipart/alternative",
            "parts": [{
                "mimeType": "application/pdf",
                "filename": "invoice.pdf",
                "body": {"attachmentId": "att456", "size": 12000},
            }],
        },
    ],
}
_atts: list[dict] = []
_walk_parts(_payload, _atts)
check("_walk_parts finds 2 PDFs",            len(_atts) == 2)
check("_walk_parts att_ids correct",         {a["attachment_id"] for a in _atts} == {"att123", "att456"})
check("_walk_parts filenames correct",       {a["filename"] for a in _atts} == {"statement.pdf", "invoice.pdf"})
check("_walk_parts ignores text/plain",      all(a["filename"].endswith(".pdf") for a in _atts))


# ---------------------------------------------------------------------------
# 3. DocAIOCR -- instantiation (no API calls)
# ---------------------------------------------------------------------------
print("\n-- DocAIOCR (no API) --")
try:
    _ocr = DocAIOCR()
    check("DocAIOCR() instantiates",          True)
    check("DocAIOCR._client is None on init", _ocr._client is None)
    check("DocAIOCR._processor_name is None", _ocr._processor_name is None)
except Exception as e:
    check("DocAIOCR instantiation", False, str(e))


# ---------------------------------------------------------------------------
# 4. detect_bank
# ---------------------------------------------------------------------------
print("\n-- detect_bank --")
from models.banking import BankCode
check("TD detected",      detect_bank("TD Canada Trust\nJan 2026 Statement")      == BankCode.TD)
check("RBC detected",     detect_bank("Royal Bank of Canada\nStatement")           == BankCode.RBC)
check("BMO detected",     detect_bank("Bank of Montreal\nPersonal Chequing")       == BankCode.BMO)
check("Scotia detected",  detect_bank("Scotiabank Advantage Banking")              == BankCode.SCOTIABANK)
check("CIBC detected",    detect_bank("CIBC Personal Banking\nChequing Account")   == BankCode.CIBC)
check("Generic fallback", detect_bank("Some Unknown Bank\nAccount Statement")      == BankCode.GENERIC)


# ---------------------------------------------------------------------------
# 5. parse_ocr_text -- TD style (month-day + running balance)
# ---------------------------------------------------------------------------
print("\n-- parse_ocr_text: TD style --")
_TD_OCR = (
    "TD Canada Trust\n"
    "Account Statement January 2026\n"
    "\n"
    "Date        Description                   Withdrawals  Deposits  Balance\n"
    "BALANCE FORWARD                                                   2,500.00\n"
    "Jan  2      PAYROLL DEPOSIT                             1,500.00  4,000.00\n"
    "Jan  5      GROCERY STORE                    45.67                3,954.33\n"
    "Jan 10      INTERAC PURCHASE SHOPPERS         23.50               3,930.83\n"
    "Jan 15      E-TRANSFER RECEIVED                          250.00   4,180.83\n"
    "Jan 28      SERVICE CHARGE                    4.50                4,176.33\n"
)
_td = parse_ocr_text(_TD_OCR, bank=BankCode.TD)
check("TD: 5 transactions parsed",          len(_td) == 5)
if _td:
    from decimal import Decimal
    check("TD: payroll is credit",          _td[0].credit == Decimal("1500.00"))
    check("TD: payroll debit is zero",      _td[0].debit  == Decimal("0"))
    check("TD: grocery is debit",           _td[1].debit  == Decimal("45.67"))
    check("TD: grocery credit is zero",     _td[1].credit == Decimal("0"))
    check("TD: balance on first txn",       _td[0].balance == Decimal("4000.00"))
    check("TD: date parsed Jan 2 2026",     _td[0].txn_date.isoformat() == "2026-01-02")
    check("TD: amounts stripped from desc", "1500" not in _td[0].description
                                            and "4000" not in _td[0].description)


# ---------------------------------------------------------------------------
# 6. parse_ocr_text -- RBC style (MM/DD/YYYY + signed amounts)
# ---------------------------------------------------------------------------
print("\n-- parse_ocr_text: RBC style --")
_RBC_OCR = (
    "Royal Bank of Canada\n"
    "Account Activity January 2026\n"
    "\n"
    "Transaction Date    Description 1               CAD$\n"
    "01/02/2026          PAYROLL DIRECT DEPOSIT       1,500.00\n"
    "01/05/2026          GROCERY STORE               -45.67\n"
    "01/10/2026          SHOPPERS DRUG MART          -23.50\n"
    "01/15/2026          E-TRANSFER RECEIVED          250.00\n"
)
_rbc = parse_ocr_text(_RBC_OCR, bank=BankCode.RBC)
check("RBC: 4 transactions parsed",         len(_rbc) == 4)
if len(_rbc) >= 4:
    check("RBC: payroll is credit",         _rbc[0].credit > 0 and _rbc[0].debit == Decimal("0"))
    check("RBC: grocery is debit (neg amt)",_rbc[1].debit  > 0 and _rbc[1].credit == Decimal("0"))
    check("RBC: date 01/02/2026 parsed",    _rbc[0].txn_date.isoformat() == "2026-01-02")


# ---------------------------------------------------------------------------
# 7. parse_ocr_text -- CIBC/generic (ISO dates, auto-detect)
# ---------------------------------------------------------------------------
print("\n-- parse_ocr_text: CIBC/auto-detect style --")
_CIBC_OCR = (
    "CIBC Personal Banking Chequing Account\n"
    "Statement Period: 2026-01-01 to 2026-01-31\n"
    "\n"
    "Date         Description                    Debit      Credit     Balance\n"
    "2026-01-03   DIRECT DEPOSIT PAYROLL                    2,000.00   5,000.00\n"
    "2026-01-07   VISA PAYMENT                  500.00                 4,500.00\n"
    "2026-01-12   ATM WITHDRAWAL                200.00                 4,300.00\n"
    "2026-01-20   INTERAC E-TRANSFER RECEIVED               150.00     4,450.00\n"
)
check("CIBC: bank auto-detected",           detect_bank(_CIBC_OCR) == BankCode.CIBC)
_cibc = parse_ocr_text(_CIBC_OCR)
check("CIBC: 4 transactions parsed",        len(_cibc) == 4)
if len(_cibc) >= 4:
    check("CIBC: first txn credit",         _cibc[0].credit == Decimal("2000.00"))
    check("CIBC: VISA payment is debit",    _cibc[1].debit  == Decimal("500.00"))
    check("CIBC: date ISO parsed",          _cibc[0].txn_date.isoformat() == "2026-01-03")


# ---------------------------------------------------------------------------
# 8. write_csv output format
# ---------------------------------------------------------------------------
print("\n-- write_csv output format --")
import csv, tempfile
if _td:
    _tmp = Path(tempfile.mktemp(suffix=".csv"))
    write_csv(_td, _tmp)
    _rows = list(csv.DictReader(_tmp.open(encoding="utf-8")))
    _tmp.unlink()

    check("CSV headers correct",            set(_rows[0].keys()) == {"Date","Description","Debit","Credit","Balance"})
    check("CSV row count matches",          len(_rows) == len(_td))
    check("CSV date is YYYY-MM-DD",         _rows[0]["Date"] == "2026-01-02")
    check("CSV credit populated (deposit)", _rows[0]["Credit"] != "")
    check("CSV debit blank (deposit)",      _rows[0]["Debit"] == "")
    check("CSV debit populated (expense)",  _rows[1]["Debit"] != "")
    check("CSV credit blank (expense)",     _rows[1]["Credit"] == "")


# ---------------------------------------------------------------------------
# 9. gmail_watcher helpers
# ---------------------------------------------------------------------------
print("\n-- gmail_watcher helpers --")
check("_resolve_client concetta",   _mod._resolve_client("concetta")["r_folder"] == "Concetta Enterprises Inc")  # type: ignore
check("_resolve_client case-insens",_mod._resolve_client("CONCETTA")["account_no"] == "xxxx5443")  # type: ignore
try:
    _mod._resolve_client("no_such_client")  # type: ignore
    check("_resolve_client unknown raises", False)
except SystemExit:
    check("_resolve_client unknown raises SystemExit", True)

check("_period_from_filename ISO",    _mod._period_from_filename("TD_2026-02_statement.pdf") == "2026-02")  # type: ignore
check("_period_from_filename word",   _mod._period_from_filename("dec-2025-bank.pdf") == "2025-12")         # type: ignore
check("_period_from_filename none",   _mod._period_from_filename("statement.pdf") is None)                  # type: ignore

import datetime as _dt
_feb_ts  = int(_dt.datetime(2026, 2, 15).timestamp() * 1000)
_jan_ts  = int(_dt.datetime(2026, 1, 10).timestamp() * 1000)
check("_period_from_epoch Feb->Jan", _mod._period_from_epoch(_feb_ts) == "2026-01")   # type: ignore
check("_period_from_epoch Jan->Dec", _mod._period_from_epoch(_jan_ts) == "2025-12")   # type: ignore


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
_total  = len(_checks)
_passed = sum(1 for _, v, _ in _checks if v)
_failed = _total - _passed
print(f"\n{'='*50}")
print(f"{_passed}/{_total} checks passed")
if _failed:
    print("\nFailed checks:")
    for _lbl, _v, _det in _checks:
        if not _v:
            print(f"  FAIL: {_lbl}" + (f"  ({_det})" if _det else ""))
    sys.exit(1)
else:
    print("All checks passed.")
