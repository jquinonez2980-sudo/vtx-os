"""
sage50/bank_statement_ocr_parser.py
Bank-agnostic Canadian bank statement OCR text parser.

Input:  plain text from Document AI (or any OCR source)
Output: CSV with columns:
            Date, Description, Debit, Credit, Balance
        Headers match CIBC format → auto-detected as BankCode.CIBC by bank_parser.py.

Supported banks (auto-detected from header text):
    TD Canada Trust, RBC, BMO, Scotiabank, CIBC, Desjardins, National Bank + generic fallback

Balance-tracking algorithm:
    When a running balance is present on each transaction line (the last amount on the
    line), the debit/credit direction is inferred from whether the balance rose or fell.
    For statements that show signed amounts (e.g. RBC -45.67), the sign is used directly.
    Unknown direction defaults to debit (conservative).

Usage:
    from sage50.bank_statement_ocr_parser import parse_and_write_csv, detect_bank

    n = parse_and_write_csv(ocr_text, "statement.csv")
    print(f"{n} transactions written")
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from models.banking import BankCode


# ---------------------------------------------------------------------------
# Bank detection
# ---------------------------------------------------------------------------

_BANK_SIGNATURES: list[tuple[re.Pattern, BankCode]] = [
    (re.compile(r"td\s+canada\s+trust|td\s+bank|toronto.dominion", re.I), BankCode.TD),
    (re.compile(r"royal\s+bank|rbc\b", re.I),                             BankCode.RBC),
    (re.compile(r"bank\s+of\s+montreal|\bbmo\b",    re.I),                BankCode.BMO),
    (re.compile(r"scotiabank|bank\s+of\s+nova\s+scotia", re.I),           BankCode.SCOTIABANK),
    (re.compile(r"\bcibc\b|canadian\s+imperial",    re.I),                BankCode.CIBC),
    (re.compile(r"desjardins",                      re.I),                BankCode.DESJARDINS),
    (re.compile(r"national\s+bank|banque\s+nationale", re.I),             BankCode.NATIONAL),
]


def detect_bank(text: str) -> BankCode:
    """Detect the issuing bank from the first 2 000 characters of OCR text."""
    sample = text[:2000]
    for pattern, bank in _BANK_SIGNATURES:
        if pattern.search(sample):
            return bank
    return BankCode.GENERIC


# ---------------------------------------------------------------------------
# Internal transaction record
# ---------------------------------------------------------------------------

@dataclass
class _Txn:
    txn_date:    date
    description: str
    debit:       Decimal
    credit:      Decimal
    balance:     Optional[Decimal]


# ---------------------------------------------------------------------------
# Amount helpers
# ---------------------------------------------------------------------------

_ZERO = Decimal("0")

# Matches optional leading minus then dollar amounts like 1,234.56 or 234.56.
# The word-boundary-like look-arounds prevent matching inside longer numbers.
_AMOUNT_RE = re.compile(r"(?<![,\d])(-?\d{1,3}(?:,\d{3})*\.\d{2})(?!\d)")


def _dec(s: str) -> Decimal:
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation:
        return _ZERO


def _extract_amounts(text: str) -> list[Decimal]:
    return [_dec(m) for m in _AMOUNT_RE.findall(text)]


def _strip_amounts(text: str) -> str:
    """Remove all amount tokens, then collapse whitespace."""
    clean = _AMOUNT_RE.sub(" ", text)
    return re.sub(r"\s{2,}", " ", clean).strip()


# ---------------------------------------------------------------------------
# Year inference
# ---------------------------------------------------------------------------

def _infer_year(text: str) -> int:
    """Return the most-common 20xx year found in the first 500 chars, or current year."""
    from collections import Counter
    hits = re.findall(r"\b(20\d{2})\b", text[:500])
    return int(Counter(hits).most_common(1)[0][0]) if hits else date.today().year


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5,  "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# "Jan 02" or "Jan  2" at line start (TD, BMO, Scotia)
_MDAY_RE = re.compile(
    r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\s+",
    re.I,
)
# "YYYY-MM-DD" or "YYYY/MM/DD"
_ISO_RE = re.compile(r"^(\d{4})[/\-](\d{2})[/\-](\d{2})\s+")
# "MM/DD/YYYY" or "DD/MM/YYYY" (ambiguous — try MM first)
_MDY_RE = re.compile(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\s+")


def _try_parse_date(line: str, year: int) -> tuple[date | None, str]:
    """Attempt to parse a date at the START of *line*.

    Returns (parsed_date, remainder_of_line) on success; (None, line) on failure.
    """
    m = _MDAY_RE.match(line)
    if m:
        month = _MONTH_MAP.get(m.group(1).lower()[:3])
        if month:
            try:
                return date(year, month, int(m.group(2))), line[m.end():]
            except ValueError:
                pass

    m = _ISO_RE.match(line)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), line[m.end():]
        except ValueError:
            pass

    m = _MDY_RE.match(line)
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
        for mo, dy in [(a, b), (b, a)]:   # try MM/DD then DD/MM
            if 1 <= mo <= 12 and 1 <= dy <= 31:
                try:
                    return date(c, mo, dy), line[m.end():]
                except ValueError:
                    continue

    return None, line


# ---------------------------------------------------------------------------
# Debit / credit classification
# ---------------------------------------------------------------------------

def _classify(
    amount: Decimal,
    prev_bal: Decimal | None,
    curr_bal: Decimal | None,
) -> tuple[Decimal, Decimal]:
    """Return (debit, credit) for *amount*.

    Priority:
      1. Signed amount (negative → debit, positive → credit when sign is present)
      2. Balance-change tracking (balance fell → debit, rose → credit)
      3. Default: treat as debit
    """
    abs_amt = abs(amount)

    # Signed amount from OCR (RBC-style "-45.67")
    if amount < _ZERO:
        return abs_amt, _ZERO
    if amount > _ZERO and prev_bal is not None and curr_bal is not None:
        change = curr_bal - prev_bal
        return (_ZERO, abs_amt) if change >= _ZERO else (abs_amt, _ZERO)
    if amount > _ZERO and prev_bal is None and curr_bal is None:
        # No balance context — positive defaults to credit (signed-column convention, e.g. RBC)
        return _ZERO, abs_amt
    # Edge case: amount > 0 with only partial balance context
    if prev_bal is not None and curr_bal is not None:
        return (_ZERO, abs_amt) if (curr_bal - prev_bal) >= _ZERO else (abs_amt, _ZERO)
    return abs_amt, _ZERO


# ---------------------------------------------------------------------------
# Column header line detection
# ---------------------------------------------------------------------------

_HEADER_KEYWORDS_RE = re.compile(
    r"\b(withdrawals?|deposits?|debit|credit|balance|description|transaction|date)\b",
    re.I,
)


def _is_header_line(line: str) -> bool:
    """Return True for column header rows that should be skipped.

    A header line has known header keywords but no monetary amounts.
    """
    return bool(_HEADER_KEYWORDS_RE.search(line)) and not _AMOUNT_RE.search(line)


# ---------------------------------------------------------------------------
# Transaction section extraction (strip cover-page boilerplate)
# ---------------------------------------------------------------------------

_TXN_START_RE = re.compile(
    r"balance\s+forward|opening\s+balance|previous\s+(statement\s+)?balance"
    r"|account\s+activity|transaction\s+history",
    re.I,
)
_TXN_END_RE = re.compile(
    r"closing\s+balance|total\s+(withdrawals?|deposits?|debits?|credits?)"
    r"|service\s+charges?\s+summary|please\s+examine",
    re.I,
)

_BAL_FORWARD_RE = re.compile(
    r"balance\s+forward|opening\s+balance|brought\s+forward",
    re.I,
)


def _extract_transaction_section(lines: list[str]) -> list[str]:
    """Slice the line list to the transaction table region.

    Falls back to the full list when section markers are absent.
    """
    start, end = 0, len(lines)
    for i, ln in enumerate(lines):
        if _TXN_START_RE.search(ln):
            start = i
            break
    for i in range(len(lines) - 1, start, -1):
        if _TXN_END_RE.search(lines[i]):
            end = i + 1
            break
    return lines[start:end]


# ---------------------------------------------------------------------------
# Description-keyword direction hints (used when balance delta is unavailable)
# ---------------------------------------------------------------------------

_CREDIT_DESC_RE = re.compile(
    r"\b(deposit|credit|received|payroll|salary|dividend|transfer\s+in"
    r"|direct\s+dep|wire\s+in|interest\s+paid)\b",
    re.I,
)
_DEBIT_DESC_RE = re.compile(
    r"\b(payment|purchase|withdrawal|withdrawn|charge|fee|debit"
    r"|transfer\s+out|cheque|check|pre.?auth)\b",
    re.I,
)


def _guess_direction(desc: str) -> str | None:
    if _CREDIT_DESC_RE.search(desc):
        return "credit"
    if _DEBIT_DESC_RE.search(desc):
        return "debit"
    return None


# ---------------------------------------------------------------------------
# Line-by-line transaction parser
# ---------------------------------------------------------------------------

def _parse_lines(lines: list[str], year: int) -> list[_Txn]:
    transactions: list[_Txn] = []
    prev_balance: Decimal | None = None

    for raw in lines:
        line = raw.strip()
        if len(line) < 6:
            continue
        if _is_header_line(line):
            continue

        txn_date, rest = _try_parse_date(line, year)
        if txn_date is None:
            # Seed prev_balance from "BALANCE FORWARD" / opening-balance lines.
            if prev_balance is None and _BAL_FORWARD_RE.search(line):
                bals = _extract_amounts(line)
                if bals:
                    prev_balance = bals[-1]
            continue

        amounts = _extract_amounts(rest)
        if not amounts:
            continue

        # When 2+ amounts are present the last is treated as the running balance.
        if len(amounts) >= 2:
            balance    = amounts[-1]
            txn_amount = amounts[-2]
        else:
            balance    = None
            txn_amount = amounts[0]

        desc = _strip_amounts(rest)
        if not desc:
            continue

        had_prev = prev_balance is not None
        debit, credit = _classify(txn_amount, prev_balance, balance)

        # When no prior balance was available the direction may be uncertain.
        # Refine using description keywords for non-negative amounts.
        if not had_prev and txn_amount >= _ZERO:
            guess = _guess_direction(desc)
            if guess == "credit":
                debit, credit = _ZERO, abs(txn_amount)
            elif guess == "debit":
                debit, credit = abs(txn_amount), _ZERO

        transactions.append(_Txn(
            txn_date=txn_date,
            description=desc,
            debit=debit,
            credit=credit,
            balance=balance,
        ))

        if balance is not None:
            prev_balance = balance

    return transactions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_ocr_text(
    text: str,
    bank: BankCode | None = None,
) -> list[_Txn]:
    """Parse bank statement OCR text into a list of transaction records.

    *bank* is auto-detected from the header text when not supplied.
    """
    if bank is None:
        bank = detect_bank(text)
    year  = _infer_year(text)
    lines = _extract_transaction_section(text.splitlines())
    return _parse_lines(lines, year)


def write_csv(transactions: list[_Txn], output_path: Path | str) -> int:
    """Write transactions to CSV and return the row count.

    Output columns:  Date, Description, Debit, Credit, Balance
    These headers are detected as BankCode.CIBC by sage50/bank_parser.py,
    which computes: amount = Credit - abs(Debit).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["Date", "Description", "Debit", "Credit", "Balance"],
        )
        writer.writeheader()
        for t in transactions:
            writer.writerow({
                "Date":        t.txn_date.strftime("%Y-%m-%d"),
                "Description": t.description,
                "Debit":       str(t.debit)   if t.debit   else "",
                "Credit":      str(t.credit)  if t.credit  else "",
                "Balance":     str(t.balance) if t.balance is not None else "",
            })
    return len(transactions)


def parse_and_write_csv(
    text: str,
    output_path: Path | str,
    bank: BankCode | None = None,
) -> int:
    """Parse OCR text and write CSV in one call. Returns the row count written."""
    return write_csv(parse_ocr_text(text, bank=bank), output_path)
