"""
sage50/gl_parser.py
Parse a Sage 50 GL transaction export CSV and return GLEntry objects
for the specified bank GL account number.

Expected columns (Sage 50 default export):
    Date, Source No., Account No., Account Description, Debit, Credit, Description

Dates are in Sage 50's native MM/DD/YYYY format.
Debit and Credit columns are positive numbers or empty strings.

Usage:
    from sage50.gl_parser import parse_gl_csv
    entries = parse_gl_csv("exports/dec2025-gl.csv", gl_bank_account="1060")
"""

from __future__ import annotations

import csv
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from models.reconciliation import GLEntry


# ---------------------------------------------------------------------------
# Helpers (mirrors patterns from bank_parser._dec / _parse_date)
# ---------------------------------------------------------------------------

def _dec(s: Any) -> Decimal:
    if not s or str(s).strip() in ("", "-", "–"):
        return Decimal("0")
    cleaned = re.sub(r"[$,\s]", "", str(s))
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return Decimal("0")


def _parse_date(s: str) -> date | None:
    s = s.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%Y%m%d"):
        try:
            import datetime as _dt
            return _dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_gl_csv(
    path: str | Path,
    gl_bank_account: str,
) -> list[GLEntry]:
    """Return all GL lines where Account No. matches gl_bank_account.

    The caller sees the net effect on the bank account for each journal entry
    line via GLEntry.gl_net_amount.
    """
    path = Path(path)
    entries: list[GLEntry] = []

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row_num, row in enumerate(reader, start=2):
            acct = row.get("Account No.", "").strip()
            if acct != gl_bank_account:
                continue

            entry_date = _parse_date(row.get("Date", ""))
            if entry_date is None:
                continue

            debit  = _dec(row.get("Debit",  ""))
            credit = _dec(row.get("Credit", ""))

            # Skip zero-effect rows (shouldn't exist in valid GL data)
            if debit == 0 and credit == 0:
                continue

            entries.append(GLEntry(
                entry_date=entry_date,
                source_no=row.get("Source No.", "").strip(),
                account_no=acct,
                account_name=row.get("Account Description", "").strip(),
                description=row.get("Description", "").strip(),
                debit=debit,
                credit=credit,
            ))

    return entries
