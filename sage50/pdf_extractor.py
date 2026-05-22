"""
sage50/pdf_extractor.py
Extracts transactions from a TD Bank Canada PDF statement using pdfplumber.
Outputs a TD-format CSV compatible with sage50.bank_parser.

Handles common OCR artifacts found in scanned TD statements:
  - Date garbling: DECO! DECll DECIO DE.C31 .DEC04
  - Amount garbling: "651,40"  "430 .14"  ";.486 .27"  "·9:4 .92"
  - Large credits: "23., 249. 07"
  - Garbled service lines: ". SERVI CE-'-CHAR.GE - 3_,,1"

Balance-chain correction fills in any amounts that cannot be parsed from
OCR text by computing the residual from stated running balances.

Usage:
    from sage50.pdf_extractor import extract_to_csv
    csv_path = extract_to_csv("data/test-client/dec-2025-bank.pdf",
                               "data/test-client/dec-2025-bank-extracted.csv")
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MIN_AMOUNT = Decimal("1.00")
_BALANCE_TOLERANCE = Decimal("0.50")   # max acceptable OCR rounding error in stated balance

_OCR_DIGIT_MAP = str.maketrans("OolIi!", "000111")

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# Pattern: 3-letter month (with possible internal period) + 2 OCR-garbled day chars
_DATE_RE = re.compile(
    r"(?:[.\s·•*:;,]*)"
    r"((?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\.?)"  # month (+ optional stray period)
    r"([A-Za-z0-9!]{2})",                                           # day (OCR may substitute)
    re.IGNORECASE,
)

# Markers that bound the transaction section
_TXN_START = re.compile(r"BALANCE\s+FORWARD", re.IGNORECASE)
_TXN_END   = re.compile(r"4\s+CHQS\s+ENCLOSED|MONTHLY\s+AVER|Credits\s+\d|Debits\s+\d", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Internal types
# ---------------------------------------------------------------------------

@dataclass
class _RawTxn:
    txn_date:       date
    raw_desc:       str
    parsed_amount:  Optional[Decimal]  # None = could not parse from OCR text
    stated_balance: Optional[Decimal]  # None = not on this line
    is_credit:      bool = False
    final_amount:   Optional[Decimal] = field(default=None, init=False)


# ---------------------------------------------------------------------------
# Amount parser
# ---------------------------------------------------------------------------

def _parse_amount(raw: str) -> Optional[Decimal]:
    """Parse a possibly OCR-garbled currency string.

    Requires exactly 2 decimal places and a value >= $1.00.
    Returns None on failure.
    """
    if not raw:
        return None

    # Normalise unicode punctuation → plain ASCII
    s = raw.replace("·", "").replace("•", "").replace("·", "")
    # Remove spaces around decimal-like characters
    s = re.sub(r"\s+", "", s)
    # Keep only digits, comma, period
    s = re.sub(r"[^0-9.,]", "", s)
    if not s:
        return None

    # Comma-decimal (exactly NNN,NN): "651,40" → "651.40"
    if re.fullmatch(r"\d+,\d{2}", s):
        s = s.replace(",", ".")
    else:
        # Strip thousands separators (comma before exactly 3 digits)
        s = re.sub(r",(?=\d{3})", "", s)
        s = s.replace(",", "")
        # Multiple decimal points — keep only the last:  "23.249.07" → "23249.07"
        if s.count(".") > 1:
            parts = s.split(".")
            s = "".join(parts[:-1]) + "." + parts[-1]

    # Must end in exactly 2 decimal digits
    if not re.search(r"\.\d{2}$", s):
        return None

    try:
        val = Decimal(s)
        return val if val >= _MIN_AMOUNT else None
    except InvalidOperation:
        return None


def _parse_balance(raw: str) -> Optional[Decimal]:
    """Parse a running balance from text after the date field.
    Accepts incomplete values (e.g. '810.4' when real is '810.47').
    """
    s = re.sub(r"[^0-9.,]", "", raw)
    if not s:
        return None
    # Thousands-separator cleanup
    s = re.sub(r",(?=\d{3})", "", s)
    s = s.replace(",", "")
    if s.count(".") > 1:
        parts = s.split(".")
        s = "".join(parts[:-1]) + "." + parts[-1]
    try:
        val = Decimal(s)
        return val if val > 0 else None
    except InvalidOperation:
        return None


# ---------------------------------------------------------------------------
# Description / amount splitter
# ---------------------------------------------------------------------------

def _split_desc_amount(before_date: str) -> tuple[str, Optional[Decimal]]:
    """Split 'DESCRIPTION  AMOUNT' from the text before the date token.

    Tries last 1 token, then last 2 tokens, as the potential amount.
    Falls back to (full_text, None) if no valid amount found.
    """
    # Strip leading / trailing OCR noise (periods, bullets, dashes)
    text = re.sub(r"^[.\s·•:;,-]+", "", before_date)
    text = re.sub(r"[\s·•:;]+$",    "", text)
    # Remove isolated trailing period
    text = re.sub(r"\s*\.$",         "", text).strip()

    tokens = text.split()
    if not tokens:
        return "", None

    for n in (1, 2):
        if len(tokens) <= n:
            continue
        candidate = " ".join(tokens[-n:])
        amount = _parse_amount(candidate)
        if amount is not None:
            desc = " ".join(tokens[:-n]).strip()
            if desc:                        # don't strip away the entire description
                return desc, amount

    # No parseable amount
    return text, None


# ---------------------------------------------------------------------------
# Date normalisation
# ---------------------------------------------------------------------------

_MONTH_PAT = "(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"

# TD Bank period header: "DEC 31/25 – JAN 30/26"  (en-dash, em-dash, or hyphen)
# \s* instead of \s+ — some PDF renderers omit the space between month and day.
_PERIOD_RE = re.compile(
    rf"({_MONTH_PAT})\s*\d{{1,2}}/(\d{{2}})\s*[–—\-]+\s*({_MONTH_PAT})\s*\d{{1,2}}/(\d{{2}})",
    re.IGNORECASE,
)


def _detect_statement_period(text: str) -> dict[int, int]:
    """Parse the statement period header into a {month_num: full_year} map.

    "DEC 31/25 – JAN 30/26"  →  {12: 2025, 1: 2026}

    Walks forward from the start month/year to the end month/year, so a
    statement spanning multiple months (e.g. NOV–JAN) is handled correctly.
    Returns {} when the header line is not found.
    """
    m = _PERIOD_RE.search(text)
    if not m:
        return {}

    start_mon = _MONTHS[m.group(1).upper()]
    start_yr  = 2000 + int(m.group(2))
    end_mon   = _MONTHS[m.group(3).upper()]
    end_yr    = 2000 + int(m.group(4))

    mapping: dict[int, int] = {}
    yr, mo = start_yr, start_mon
    for _ in range(13):           # guard: at most 12 months in any statement
        mapping[mo] = yr
        if mo == end_mon and yr == end_yr:
            break
        mo = mo % 12 + 1
        if mo == 1:
            yr += 1

    return mapping


def _normalize_date(
    month_raw: str,
    day_raw: str,
    year: int,
    period_map: dict[int, int] | None = None,
) -> Optional[date]:
    """Convert OCR-garbled month+day tokens into a date.

    *period_map* (built from the statement header) takes precedence over the
    scalar *year* so that cross-year statements (e.g. DEC 2025 / JAN 2026)
    assign the correct year to each month.
    """
    # Strip internal stray period from month (DE.C → DEC)
    month_clean = re.sub(r"[^A-Za-z]", "", month_raw).upper()
    month_num = _MONTHS.get(month_clean)
    if month_num is None:
        return None
    resolved_year = (period_map or {}).get(month_num, year)
    day_str = day_raw.upper().translate(_OCR_DIGIT_MAP)
    if not day_str.isdigit():
        return None
    d = int(day_str)
    if not 1 <= d <= 31:
        return None
    try:
        return date(resolved_year, month_num, d)
    except ValueError:
        return None


def _preprocess_line(line: str) -> str:
    """Normalise OCR artifacts in a line before regex matching."""
    # Fix period embedded in month name: DE.C31 → DEC31
    line = re.sub(r"\bDE\.C\b", "DEC", line, flags=re.IGNORECASE)
    line = re.sub(r"\bJA\.N\b", "JAN", line, flags=re.IGNORECASE)
    # Fix bullet/middle-dot before month: ·DEC → DEC (keep space)
    line = re.sub(r"[·•]\s*(DEC|JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV)", r" \1", line, flags=re.IGNORECASE)
    return line


# ---------------------------------------------------------------------------
# Core parsing
# ---------------------------------------------------------------------------

def _extract_text(pdf_path: Path) -> tuple[str, str]:
    """Return (full_text, txn_text) opening the PDF once.

    full_text: every page joined — used for period-header detection so the
               account-summary page is always searched even when it lacks
               "BALANCE FORWARD".
    txn_text:  only transaction-bearing pages — used for line-by-line parsing.
    """
    import pdfplumber
    full_parts: list[str] = []
    txn_parts: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            full_parts.append(text)
            if re.search(r"BALANCE\s+FORWARD|DESCRIPTION", text, re.IGNORECASE):
                txn_parts.append(text)
    return "\n".join(full_parts), "\n".join(txn_parts)


def _parse_raw_txns(
    text: str,
    year: int,
    period_map: dict[int, int] | None = None,
) -> tuple[Decimal, list[_RawTxn]]:
    """Extract opening balance and raw transaction records from statement text."""
    opening_balance = Decimal("0")
    raw_txns: list[_RawTxn] = []
    in_txns = False

    for raw_line in text.splitlines():
        line = _preprocess_line(raw_line)

        # Opening balance
        if _TXN_START.search(line):
            m = re.search(r"([\d,]+\.\d{2})\s*$", line)
            if m:
                opening_balance = _parse_balance(m.group(1)) or Decimal("0")
            in_txns = True
            continue

        if not in_txns:
            continue
        if _TXN_END.search(line):
            break
        if not line.strip():
            continue

        # Find date token in line
        dm = _DATE_RE.search(line)
        if not dm:
            continue

        txn_date = _normalize_date(dm.group(1), dm.group(2), year, period_map)
        if txn_date is None:
            continue

        # Text before date → description + amount
        before_date = line[:dm.start()]
        # Text after date → optional running balance
        after_date  = line[dm.end():].strip()
        # Strip leading OCR noise from after_date before parsing balance
        after_date_clean = re.sub(r"^[^0-9]*", "", after_date)
        stated_balance = _parse_balance(after_date_clean) if after_date_clean else None

        desc, amount = _split_desc_amount(before_date)

        # Clean description
        desc = re.sub(r"\s+", " ", desc).strip()
        # Remove isolated leading/trailing punctuation chars
        desc = re.sub(r"^[^A-Za-z0-9]+", "", desc)
        desc = re.sub(r"[^A-Za-z0-9)]+$", "", desc)

        if not desc:
            continue

        raw_txns.append(_RawTxn(
            txn_date=txn_date,
            raw_desc=desc,
            parsed_amount=amount,
            stated_balance=stated_balance,
        ))

    return opening_balance, raw_txns


# ---------------------------------------------------------------------------
# Balance-chain correction
# ---------------------------------------------------------------------------

def _resolve_amounts(
    raw_txns: list[_RawTxn],
    opening_balance: Decimal,
) -> None:
    """Fill in None amounts and detect credits using the running balance chain.

    Mutates raw_txns in place, setting final_amount and is_credit.
    """
    running = opening_balance
    group_start = 0

    def _flush_group(end_exclusive: int, stated: Decimal) -> None:
        nonlocal running
        group = raw_txns[group_start:end_exclusive]
        expected_change = running - stated          # positive = net debit, negative = net credit

        known     = [t for t in group if t.parsed_amount is not None]
        unknowns  = [t for t in group if t.parsed_amount is None]

        known_sum = sum(t.parsed_amount for t in known)  # type: ignore[arg-type]

        if unknowns:
            residual = abs(expected_change) - known_sum
            if len(unknowns) == 1:
                unknowns[0].parsed_amount = residual
            # Multi-unknown: split evenly as best-effort
            elif len(unknowns) > 1 and residual > 0:
                share = residual / len(unknowns)
                for u in unknowns:
                    u.parsed_amount = share.quantize(Decimal("0.01"))
        else:
            # All amounts parsed — verify against chain; override on large mismatch
            if len(group) == 1 and known_sum > 0:
                diff = abs(abs(expected_change) - known_sum)
                if diff > _BALANCE_TOLERANCE:
                    group[0].parsed_amount = abs(expected_change)

        # Mark credits (balance increased)
        is_net_credit = expected_change < 0
        for t in group:
            if is_net_credit and len(group) == 1:
                t.is_credit = True
            elif is_net_credit:
                # In a mixed group, use balance direction as proxy
                t.is_credit = True
            t.final_amount = t.parsed_amount

        running = stated

    for i, txn in enumerate(raw_txns):
        if txn.stated_balance is not None:
            # Verify the stated balance looks reasonable
            stated = txn.stated_balance
            _flush_group(i + 1, stated)
            group_start = i + 1

    # Remaining ungrouped transactions (no trailing balance)
    remainder = raw_txns[group_start:]
    for t in remainder:
        t.final_amount = t.parsed_amount or Decimal("0")


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------

def extract_to_csv(
    pdf_path: str | Path,
    csv_path: str | Path,
    year: int | None = None,
    account_no: str = "xxxx",
) -> Path:
    """Extract transactions from a TD Bank PDF and write a TD-format CSV.

    *year* is only a fallback for months not covered by the statement period
    header.  Leave it as None (the default) to auto-detect from the header
    line "DEC 31/25 – JAN 30/26"; pass an explicit value only when the header
    is absent or malformed (e.g. when processing test fixtures).

    Returns the path to the written CSV.
    """
    pdf_path = Path(pdf_path)
    csv_path = Path(csv_path)

    full_text, text = _extract_text(pdf_path)

    # Use full_text so the period header is found even on non-transaction pages.
    period_map = _detect_statement_period(full_text)

    # Resolve fallback year: prefer end of detected period, then caller arg,
    # then the current calendar year.
    if year is None:
        year = max(period_map.values()) if period_map else date.today().year

    opening_balance, raw_txns = _parse_raw_txns(text, year, period_map)
    _resolve_amounts(raw_txns, opening_balance)

    # Try to extract account number from text if not supplied
    if account_no == "xxxx":
        m = re.search(r"\b(\d{4}-\d{7})\b", full_text)
        if m:
            account_no = "xxxx" + m.group(1)[-4:]

    # Try to extract client name
    client_name = ""
    for line in full_text.splitlines():
        line = line.strip()
        if re.search(r"(INC\.|LTD\.|CORP\.|ENT(ERPRISE)?S?)", line, re.IGNORECASE):
            if len(line) < 60 and not re.search(r"BANK|TRUST|ACCOUNT", line, re.IGNORECASE):
                client_name = line
                break

    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        # Preamble (compatible with bank_parser preamble-skip logic)
        writer.writerow(["Account Number", account_no])
        writer.writerow(["Account Type", "Business Chequing"])
        if client_name:
            writer.writerow(["Client", client_name])
        writer.writerow([])
        # Header
        writer.writerow(["Date", "Description", "Withdrawals ($)", "Deposits ($)", "Balance ($)"])

        prev_balance: Optional[Decimal] = None
        for t in raw_txns:
            if t.final_amount is None:
                continue
            date_str    = t.txn_date.strftime("%Y-%m-%d")
            withdrawal  = "" if t.is_credit else str(t.final_amount)
            deposit     = str(t.final_amount) if t.is_credit else ""
            balance_str = str(t.stated_balance) if t.stated_balance else ""
            writer.writerow([date_str, t.raw_desc, withdrawal, deposit, balance_str])
            prev_balance = t.stated_balance or prev_balance

    return csv_path


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python -m sage50.pdf_extractor <input.pdf> <output.csv> [year-override]")
        sys.exit(1)
    _year = int(sys.argv[3]) if len(sys.argv) > 3 else None
    out = extract_to_csv(sys.argv[1], sys.argv[2], year=_year)
    print(f"Wrote {out}")
