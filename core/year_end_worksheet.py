"""
core/year_end_worksheet.py
Populate the year-end Excel worksheet template with a Sage 50 trial balance.

Template structure (R:\\Templates\\TEMPLATE_YearEnd_Accounting_Professional_BLANK_v2.xlsx):
  "0. Cover Sheet"  — D8=client, D9=year_end_date, D10=prepared_by, D11=date_prepared
  "1. Worksheet"    — rows 2-204, cols A-D (E-M are formula-driven, must not be touched)
  "2. Adjusting Entries", "Income Statement", "Balance Sheet" — formula-driven, untouched
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import openpyxl

from sage50.trial_balance_parser import TBLine


def populate_worksheet(
    template_path: Path,
    output_path: Path,
    client_name: str,
    year_end_date: str,
    tb_lines: list[TBLine],
    prepared_by: str = "Jorge Quinonez CPA",
) -> Path:
    """Populate the year-end Excel template with trial balance data.

    Writes Cover Sheet metadata (D8-D11) and Worksheet tab TB columns (A-D).
    Columns E-M of the Worksheet tab contain pre-built formulas — never touched.

    Args:
        template_path:  Path to the blank Excel template.
        output_path:    Destination path for the populated workbook.
        client_name:    e.g. "Concetta Enterprises Inc."
        year_end_date:  Human-readable date string, e.g. "April 30, 2026"
        tb_lines:       Parsed trial balance lines (from parse_trial_balance).
        prepared_by:    Preparer name written to D10.

    Returns:
        output_path (same value passed in, for caller convenience).

    Raises:
        FileNotFoundError if template_path does not exist.
        ValueError if expected worksheet tabs are missing from the template.
    """
    if not template_path.exists():
        raise FileNotFoundError(f"Year-end template not found: {template_path}")

    wb = openpyxl.load_workbook(template_path)

    # --- Cover Sheet ---
    cover_tab = "0. Cover Sheet"
    if cover_tab not in wb.sheetnames:
        raise ValueError(
            f"Template is missing tab '{cover_tab}'. "
            f"Found tabs: {wb.sheetnames}"
        )
    cover = wb[cover_tab]
    cover["D8"] = client_name
    cover["D9"] = year_end_date
    cover["D10"] = prepared_by
    cover["D11"] = date.today().strftime("%B %d, %Y")

    # --- Worksheet tab ---
    ws_tab = "1. Worksheet"
    if ws_tab not in wb.sheetnames:
        raise ValueError(
            f"Template is missing tab '{ws_tab}'. "
            f"Found tabs: {wb.sheetnames}"
        )
    ws = wb[ws_tab]

    for i, line in enumerate(tb_lines):
        row = i + 2   # data starts at row 2
        ws.cell(row=row, column=1).value = line.account_no
        ws.cell(row=row, column=2).value = line.description
        ws.cell(row=row, column=3).value = float(line.debit)  if line.debit  else None
        ws.cell(row=row, column=4).value = float(line.credit) if line.credit else None
        # Columns 5-13 (E-M) are pre-built formulas — do not touch

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return output_path
