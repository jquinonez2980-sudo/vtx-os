"""
Bank statement CSV parser for major Canadian banks.

Supported formats (auto-detected from header row):
  RBC        — "Account Number","Transaction Date","Description 1","Description 2","CAD$","USD$"
  TD         — "Date","Description","Withdrawals ($)","Deposits ($)","Balance"
  BMO        — "Date","Description","Withdrawal","Deposit","Balance"
  CIBC       — "Date","Description","Debit","Credit","Balance"
  Scotiabank — "Date","Transaction","Funds Out","Funds In","Balance"
  National   — "Date","Description","Withdrawals","Deposits","Balance"
  Desjardins — "No","Date","Description","Withdrawal","Deposit","Balance"
  Generic    — falls back to heuristic column detection

Usage:
    from sage50.bank_parser import parse_csv
    from models.banking import BankCode

    txns = parse_csv("C:/exports/rbc_dec2025.csv", account_no="xxxx1234")
"""

from __future__ import annotations

import csv
import hashlib
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from models.banking import BankCode, BankTransaction

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dec(s: Any) -> Decimal:
    if not s or str(s).strip() in ("", "-", "–"):
        return Decimal("0")
    cleaned = re.sub(r"[$,\s]", "", str(s))
    # handle parentheses for negatives: (123.45) → -123.45
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = "-" + cleaned[1:-1]
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return Decimal("0")


def _parse_date(s: str) -> date | None:
    s = s.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y",
                "%b %d, %Y", "%d-%b-%Y", "%Y%m%d"):
        try:
            import datetime as _dt
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _txn_id(bank: str, account: str, d: date, desc: str, amount: Decimal, idx: int) -> str:
    key = f"{bank}|{account}|{d}|{desc}|{amount}|{idx}"
    return hashlib.sha256(key.encode()).hexdigest()[:20]


def _clean_desc(s: str) -> str:
    """Remove excessive whitespace and common bank noise from description."""
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"#\d{4,}", "", s)           # remove card numbers
    s = re.sub(r"\b\d{6,}\b", "", s)        # remove long numeric references
    return s.strip()


# ---------------------------------------------------------------------------
# Bank format detection
# ---------------------------------------------------------------------------

# Signature strings found in header rows → BankCode
_SIGNATURES: list[tuple[set[str], BankCode]] = [
    ({"transaction date", "description 1", "cad$"},             BankCode.RBC),
    ({"withdrawals ($)", "deposits ($)"},                        BankCode.TD),
    ({"funds out", "funds in", "transaction"},                   BankCode.SCOTIABANK),
    ({"withdrawal", "deposit", "no"},                            BankCode.DESJARDINS),
    # BMO and CIBC have similar headers — distinguish by debit/credit vs withdrawal/deposit
    ({"debit", "credit", "description"},                         BankCode.CIBC),
    ({"withdrawal", "deposit", "description"},                   BankCode.BMO),
    ({"withdrawals", "deposits", "description"},                 BankCode.NATIONAL),
]


def _detect_bank(headers: list[str]) -> BankCode:
    lower = {h.lower().strip() for h in headers}
    for signature, bank in _SIGNATURES:
        if signature.issubset(lower):
            return bank
    return BankCode.GENERIC


# ---------------------------------------------------------------------------
# Per-bank row parsers  →  (txn_date, description, raw_description, amount, balance, reference)
# ---------------------------------------------------------------------------

def _parse_rbc(row: dict, idx: int) -> tuple:
    d = _parse_date(row.get("Transaction Date", ""))
    desc1 = row.get("Description 1", "")
    desc2 = row.get("Description 2", "")
    raw = f"{desc1} {desc2}".strip()
    amount = _dec(row.get("CAD$", ""))
    return d, _clean_desc(raw), raw, amount, None, None


def _parse_td(row: dict, idx: int) -> tuple:
    d = _parse_date(row.get("Date", ""))
    raw = row.get("Description", "")
    withdrawals = abs(_dec(row.get("Withdrawals ($)", "")))
    deposits    = _dec(row.get("Deposits ($)", ""))
    amount = deposits - withdrawals
    bal = _dec(row.get("Balance", "")) or None
    return d, _clean_desc(raw), raw, amount, bal, None


def _parse_bmo(row: dict, idx: int) -> tuple:
    d = _parse_date(row.get("Date", ""))
    raw = row.get("Description", "")
    amount = _dec(row.get("Deposit", "")) - abs(_dec(row.get("Withdrawal", "")))
    bal = _dec(row.get("Balance", "")) or None
    return d, _clean_desc(raw), raw, amount, bal, None


def _parse_cibc(row: dict, idx: int) -> tuple:
    d = _parse_date(row.get("Date", ""))
    raw = row.get("Description", "")
    amount = _dec(row.get("Credit", "")) - abs(_dec(row.get("Debit", "")))
    bal = _dec(row.get("Balance", "")) or None
    return d, _clean_desc(raw), raw, amount, bal, None


def _parse_scotiabank(row: dict, idx: int) -> tuple:
    d = _parse_date(row.get("Date", ""))
    raw = row.get("Transaction", "")
    amount = _dec(row.get("Funds In", "")) - abs(_dec(row.get("Funds Out", "")))
    bal = _dec(row.get("Balance", "")) or None
    return d, _clean_desc(raw), raw, amount, bal, None


def _parse_national(row: dict, idx: int) -> tuple:
    d = _parse_date(row.get("Date", ""))
    raw = row.get("Description", "")
    amount = _dec(row.get("Deposits", "")) - abs(_dec(row.get("Withdrawals", "")))
    bal = _dec(row.get("Balance", "")) or None
    return d, _clean_desc(raw), raw, amount, bal, None


def _parse_desjardins(row: dict, idx: int) -> tuple:
    d = _parse_date(row.get("Date", ""))
    raw = row.get("Description", "")
    amount = _dec(row.get("Deposit", "")) - abs(_dec(row.get("Withdrawal", "")))
    bal = _dec(row.get("Balance", "")) or None
    ref = row.get("No", None)
    return d, _clean_desc(raw), raw, amount, bal, ref


def _parse_generic(row: dict, idx: int) -> tuple:
    """Heuristic fallback: find date-like, description-like, and amount-like columns."""
    headers_lower = {k.lower(): k for k in row}
    # date
    date_key = next((headers_lower[k] for k in headers_lower
                     if any(x in k for x in ("date",))), None)
    d = _parse_date(row[date_key]) if date_key else None
    # description
    desc_key = next((headers_lower[k] for k in headers_lower
                     if any(x in k for x in ("description", "memo", "narration", "details"))), None)
    raw = row[desc_key] if desc_key else ""
    # amount: look for single amount column or debit/credit pair
    credit_key = next((headers_lower[k] for k in headers_lower
                       if any(x in k for x in ("credit", "deposit", "in"))), None)
    debit_key = next((headers_lower[k] for k in headers_lower
                      if any(x in k for x in ("debit", "withdrawal", "out"))), None)
    amount_key = next((headers_lower[k] for k in headers_lower
                       if k == "amount"), None)
    if credit_key and debit_key:
        amount = _dec(row[credit_key]) - abs(_dec(row[debit_key]))
    elif amount_key:
        amount = _dec(row[amount_key])
    else:
        amount = Decimal("0")
    bal_key = next((headers_lower[k] for k in headers_lower if "balance" in k), None)
    bal = _dec(row[bal_key]) if bal_key else None
    return d, _clean_desc(raw), raw, amount, bal, None


_PARSERS = {
    BankCode.RBC:        _parse_rbc,
    BankCode.TD:         _parse_td,
    BankCode.BMO:        _parse_bmo,
    BankCode.CIBC:       _parse_cibc,
    BankCode.SCOTIABANK: _parse_scotiabank,
    BankCode.NATIONAL:   _parse_national,
    BankCode.DESJARDINS: _parse_desjardins,
    BankCode.GENERIC:    _parse_generic,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_csv(
    path: str | Path,
    account_no: str = "xxxx",
    bank_code: BankCode | None = None,
) -> list[BankTransaction]:
    """Parse a bank statement CSV into BankTransaction instances.

    bank_code is auto-detected from the header if not supplied.
    account_no should be the last-4 digits or a masked identifier — never
    store a full account number.
    """
    path = Path(path)
    transactions: list[BankTransaction] = []

    with open(path, newline="", encoding="utf-8-sig") as fh:
        # Skip non-header preamble rows (some banks prepend account info)
        lines = fh.readlines()

    # Find the header row (first row containing date/description-like tokens)
    header_idx = 0
    for i, line in enumerate(lines):
        tokens = {t.lower().strip().strip('"') for t in line.split(",")}
        if any(t in tokens for t in ("date", "transaction date", "no")):
            header_idx = i
            break

    import io
    csv_data = io.StringIO("".join(lines[header_idx:]))
    reader = csv.DictReader(csv_data)
    headers = reader.fieldnames or []

    detected = bank_code or _detect_bank(list(headers))
    parser = _PARSERS.get(detected, _parse_generic)

    for idx, row in enumerate(reader):
        # Skip blank / subtotal rows
        if not any(str(v).strip() for v in row.values()):
            continue

        try:
            txn_date, desc, raw, amount, balance, reference = parser(row, idx)
        except Exception:
            continue

        if txn_date is None or (amount == Decimal("0") and not desc):
            continue

        txn = BankTransaction(
            txn_id=_txn_id(detected.value, account_no, txn_date or date.today(),
                           desc, amount, idx),
            bank_code=detected,
            account_no=account_no,
            txn_date=txn_date or date.today(),
            description=desc,
            raw_description=raw,
            amount=amount,
            balance=balance,
            reference=reference,
        )
        transactions.append(txn)

    return transactions
