"""
sage50/trial_balance_parser.py
Parse Sage 50 trial balance CSV exports for year-end worksheet generation.

Sage 50 exports the trial balance via:
    Reports -> Financials -> Trial Balance -> as at <date> -> Export -> CSV

The exported file is typically named and dropped as:
    R:\\<client_folder>\\drop\\tb-{YYYY}-{MM}.csv   (e.g. tb-2026-04.csv)

Supported formats:
  1. Standard headers:  "Account No.,Account Description,Debit,Credit"
  2. No header row:     bare rows of account data
  3. Company-name preamble (like Sage 50's COA export) — auto-skipped

Only posting accounts are returned. Header rows (Type=H) and total rows (Type=T)
from the COA structure are skipped; rows where account_no is blank or non-numeric
are discarded.

Usage:
    from sage50.trial_balance_parser import parse_trial_balance, find_tb_csv
    lines = parse_trial_balance(Path("tb-2026-04.csv"))
    # or let the parser locate the file:
    csv_path = find_tb_csv(client_drop_dir, period="2026-04")
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class TBLine:
    account_no:  str      # e.g. "1100" — kept as string to preserve leading zeros
    description: str
    debit:       Decimal  # 0 if blank or not applicable
    credit:      Decimal  # 0 if blank or not applicable


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_NUMERIC_ACCT_RE = re.compile(r"^\d{3,6}$")   # 3–6 digit account codes


def _to_decimal(raw: str) -> Decimal:
    """Convert a cell value to Decimal, returning 0 on blank or unparseable input."""
    cleaned = re.sub(r"[,$\s]", "", raw or "")
    if not cleaned:
        return Decimal("0")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return Decimal("0")


def _is_data_row(row: dict | list) -> bool:
    """Return True if this row contains a valid posting-account number."""
    if isinstance(row, dict):
        acct = (row.get("Account No.") or row.get("Account") or "").strip()
    else:
        acct = (row[0] if row else "").strip()
    return bool(_NUMERIC_ACCT_RE.match(acct))


# ---------------------------------------------------------------------------
# Column detection
# ---------------------------------------------------------------------------

_HEADER_VARIANTS = {
    "account_no":  ("Account No.", "Account No", "Account", "Acct"),
    "description": ("Account Description", "Description", "Account Name", "Name"),
    "debit":       ("Debit", "Dr", "Debit Amount"),
    "credit":      ("Credit", "Cr", "Credit Amount"),
}


def _detect_columns(fieldnames: list[str]) -> dict[str, str]:
    """Map canonical keys to actual column names from the CSV header row."""
    mapping: dict[str, str] = {}
    normalized = {f.strip().lower(): f for f in fieldnames}
    for key, variants in _HEADER_VARIANTS.items():
        for v in variants:
            if v.lower() in normalized:
                mapping[key] = normalized[v.lower()]
                break
    return mapping


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_trial_balance(csv_path: Path) -> list[TBLine]:
    """Parse a Sage 50 trial balance CSV export.

    Returns a list of TBLine for posting accounts only (skips blank,
    non-numeric, header, or total rows). The list is sorted by account_no.

    Raises:
        FileNotFoundError if csv_path does not exist.
        ValueError if the file is empty or no data rows are found.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"Trial balance CSV not found: {csv_path}")

    lines: list[TBLine] = []

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
        raw = fh.read()

    # Strip BOM and leading non-data lines (company name, blank rows, etc.)
    text_lines = raw.splitlines()
    data_start = 0
    for i, line in enumerate(text_lines):
        stripped = line.strip().strip('"')
        # Skip blank lines and lines that look like a company name preamble
        if not stripped:
            data_start = i + 1
            continue
        # If the first non-blank word looks like a header or company name stop skipping
        if re.match(r"^\d{3,6}", stripped):
            data_start = i
            break
        # Check if it's a proper CSV header
        if re.search(r"account|debit|credit", stripped, re.I):
            data_start = i
            break
        data_start = i + 1

    cleaned = "\n".join(text_lines[data_start:])
    reader = csv.DictReader(cleaned.splitlines())

    if reader.fieldnames:
        col_map = _detect_columns(list(reader.fieldnames))
        acct_col  = col_map.get("account_no", "Account No.")
        desc_col  = col_map.get("description", "Account Description")
        debit_col = col_map.get("debit", "Debit")
        credit_col = col_map.get("credit", "Credit")

        for row in reader:
            acct = (row.get(acct_col) or "").strip()
            if not _NUMERIC_ACCT_RE.match(acct):
                continue
            lines.append(TBLine(
                account_no=acct,
                description=(row.get(desc_col) or "").strip(),
                debit=_to_decimal(row.get(debit_col) or ""),
                credit=_to_decimal(row.get(credit_col) or ""),
            ))
    else:
        # No header — treat first 4 columns as acct, desc, debit, credit
        for raw_row in csv.reader(cleaned.splitlines()):
            if not raw_row:
                continue
            acct = raw_row[0].strip()
            if not _NUMERIC_ACCT_RE.match(acct):
                continue
            lines.append(TBLine(
                account_no=acct,
                description=raw_row[1].strip() if len(raw_row) > 1 else "",
                debit=_to_decimal(raw_row[2] if len(raw_row) > 2 else ""),
                credit=_to_decimal(raw_row[3] if len(raw_row) > 3 else ""),
            ))

    if not lines:
        raise ValueError(f"No posting accounts found in {csv_path.name}")

    lines.sort(key=lambda l: l.account_no)
    return lines


def find_tb_csv(drop_dir: Path, period: str) -> Path:
    """Locate the trial balance CSV for a given period in the client's drop folder.

    Looks for files matching:
        tb-{period}.csv         e.g. tb-2026-04.csv
        trial-balance-{period}.csv
        tb_{period}.csv

    Raises FileNotFoundError with instructions if no match found.
    """
    candidates = [
        drop_dir / f"tb-{period}.csv",
        drop_dir / f"trial-balance-{period}.csv",
        drop_dir / f"tb_{period}.csv",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError(
        f"Trial balance CSV not found for period {period} in {drop_dir}.\n"
        f"Expected one of:\n"
        + "\n".join(f"  {p}" for p in candidates)
        + "\n\nExport from Sage 50: Reports -> Financials -> Trial Balance "
          f"-> as at {period} -> Export -> CSV, then save as tb-{period}.csv "
          f"in the drop folder."
    )
