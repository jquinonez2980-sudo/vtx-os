"""
agents/prepare_hst_return.py
PrepareHSTReturnAgent — compute CRA GST34 return lines from BigQuery GL data.

Handles TaskType.PREPARE_HST_RETURN.

Required payload keys:
    return_period   (str)  — "YYYY-MM" for the filing period

Optional payload keys:
    csv_output_path (str)  — write a Tax Summary CSV at this path
    business_no     (str)  — CRA Business Number, e.g. "123456789RT0001"
    tax_code        (str)  — default "H" (Ontario HST 13%)
    tax_rate        (str)  — decimal rate string; defaults per tax_code

Data sources (queried in order, first non-zero result wins):
    1. vtx_accounting.gl_transactions            (Sage 50 ODBC export)
    2. vtx_accounting.bank_transactions_categorized  (bank statement pipeline)

GL account convention:
    Revenue  (4xxx): credits → taxable_sales   → GST34 line 101 / 103
    Expense  (5xxx): debits  → taxable_purchases → GST34 line 106

Returns TaskResult.output as an HSTReturnSummary dict with:
    return_period, period_start, period_end, business_no, province,
    filing_due_date, line_101_total_revenue, line_103_hst_collected,
    line_106_itc_claimed, line_109_net_tax, is_refund,
    tax_codes_applied, line_count, bq_lines_table
"""

from __future__ import annotations

import calendar
import csv
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

from agents.base import AgentBase, TaskRequest, TaskResult, TaskType
from core.audit import write
from core.bq_loader import ensure_table, load_rows
from models.base import AuditRecord, EventStatus, EventType
from models.hst_return import HSTReturnLine, HSTReturnSummary

DATASET   = "vtx_accounting"
HST_TABLE = "hst_returns"
PROJECT   = os.environ.get("GOOGLE_CLOUD_PROJECT", "vtx-accounting-os-prod")

_HST_CFG = {
    "partition_field": "period_end",
    "cluster_fields":  ["return_period", "tax_code"],
}

_PROVINCE_MAP: dict[str, str] = {
    "H":     "ON",
    "HST":   "ON",
    "G":     "FED",
    "GST":   "FED",
    "Q":     "QC",
    "QST":   "QC",
    "PST":   "BC",
    "BCPST": "BC",
}

_TAX_DESCRIPTIONS: dict[str, str] = {
    "H":     "Ontario Harmonized Sales Tax (13%)",
    "HST":   "Ontario Harmonized Sales Tax (13%)",
    "G":     "Goods and Services Tax (5%)",
    "GST":   "Goods and Services Tax (5%)",
    "Q":     "Quebec Sales Tax (9.975%)",
    "QST":   "Quebec Sales Tax (9.975%)",
    "PST":   "British Columbia Provincial Sales Tax (7%)",
}

_DEFAULT_RATES: dict[str, str] = {
    "H":   "0.13",
    "HST": "0.13",
    "G":   "0.05",
    "GST": "0.05",
    "Q":   "0.09975",
    "QST": "0.09975",
    "PST": "0.07",
}


def _filing_due_date(period_end: date) -> date:
    """Last day of the month following period_end (CRA monthly GST/HST filer rule)."""
    year  = period_end.year
    month = period_end.month + 1
    if month > 12:
        month, year = 1, year + 1
    return date(year, month, calendar.monthrange(year, month)[1])


def _query_gl_totals(period_start: date, period_end: date) -> tuple[Decimal, Decimal]:
    """Return (taxable_sales, taxable_purchases) for the period.

    Tries vtx_accounting.gl_transactions first (Sage 50 ODBC export).  When
    that table has no rows for the period (e.g. before the next scheduled
    export), falls back to vtx_accounting.bank_transactions_categorized.

    Revenue  = sum of credits on GL 4xxx accounts.
    Expenses = sum of debits  on GL 5xxx accounts.
    """
    from google.cloud import bigquery
    client = bigquery.Client(project=PROJECT)
    ps = period_start.isoformat()
    pe = period_end.isoformat()

    # Primary: Sage 50 GL export
    gl_sql = f"""
        SELECT
          COALESCE(SUM(CASE WHEN STARTS_WITH(account_no, '4') THEN credit ELSE 0 END), 0)
              AS revenue_credits,
          COALESCE(SUM(CASE WHEN STARTS_WITH(account_no, '5') THEN debit  ELSE 0 END), 0)
              AS expense_debits
        FROM {DATASET}.gl_transactions
        WHERE transaction_date BETWEEN '{ps}' AND '{pe}'
    """
    row = list(client.query(gl_sql).result())[0]
    rev = Decimal(str(row.revenue_credits)).quantize(Decimal("0.01"))
    exp = Decimal(str(row.expense_debits)).quantize(Decimal("0.01"))
    if rev or exp:
        return rev, exp

    # Fallback: categorized bank transactions
    cat_sql = f"""
        SELECT
          COALESCE(SUM(CASE WHEN STARTS_WITH(gl_account_no, '4') AND amount > 0
                            THEN amount ELSE 0 END), 0)
              AS revenue_credits,
          COALESCE(SUM(CASE WHEN STARTS_WITH(gl_account_no, '5') AND amount < 0
                            THEN ABS(amount) ELSE 0 END), 0)
              AS expense_debits
        FROM {DATASET}.bank_transactions_categorized
        WHERE txn_date BETWEEN '{ps}' AND '{pe}'
    """
    row2 = list(client.query(cat_sql).result())[0]
    rev2 = Decimal(str(row2.revenue_credits)).quantize(Decimal("0.01"))
    exp2 = Decimal(str(row2.expense_debits)).quantize(Decimal("0.01"))
    return rev2, exp2


def _read_tax_csv(csv_path: str | Path) -> dict:
    """Parse a Sage 50 Tax Summary CSV; return amounts as Decimals."""
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            return {
                "taxable_sales":     Decimal(row.get("Taxable Sales",     "0") or "0"),
                "tax_collected":     Decimal(row.get("Tax Collected",     "0") or "0"),
                "taxable_purchases": Decimal(row.get("Taxable Purchases", "0") or "0"),
                "itc_claimed":       Decimal(row.get("Input Tax Credits", "0") or "0"),
                "net_tax":           Decimal(row.get("Net Tax",           "0") or "0"),
                "tax_code":          (row.get("Tax Code") or "H").strip(),
            }
    return {}


def _write_hst_csv(csv_path: str | Path, line: HSTReturnLine) -> None:
    """Write a single-row Tax Summary CSV for the period."""
    p = Path(csv_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "Period Start", "Period End", "Tax Code", "Description",
            "Taxable Sales", "Tax Collected", "Taxable Purchases",
            "Input Tax Credits", "Net Tax",
        ])
        writer.writerow([
            line.period_start.strftime("%m/%d/%Y"),
            line.period_end.strftime("%m/%d/%Y"),
            line.tax_code,
            line.tax_description,
            str(line.taxable_sales),
            str(line.tax_collected),
            str(line.taxable_purchases),
            str(line.itc_claimed),
            str(line.line_net_tax),
        ])


class PrepareHSTReturnAgent(AgentBase):
    agent_id = "prepare-hst-return-agent"

    def handle(self, request: TaskRequest) -> TaskResult:
        payload         = request.payload
        return_period   = payload["return_period"]       # "YYYY-MM"
        business_no     = payload.get("business_no")
        csv_output_path = payload.get("csv_output_path")
        tax_code        = payload.get("tax_code", "H")
        tax_rate        = Decimal(payload.get(
            "tax_rate",
            _DEFAULT_RATES.get(tax_code, "0.13"),
        ))

        # --- 1. Resolve period dates ---
        year, month  = int(return_period[:4]), int(return_period[5:7])
        period_start = date(year, month, 1)
        period_end   = date(year, month, calendar.monthrange(year, month)[1])

        # --- 2. Pull GL totals (CSV when provided; BQ otherwise) ---
        tax_csv_path = payload.get("tax_csv_path")
        if tax_csv_path:
            csv_data         = _read_tax_csv(tax_csv_path)
            taxable_sales    = csv_data["taxable_sales"]
            taxable_purchases= csv_data["taxable_purchases"]
            tax_collected    = csv_data["tax_collected"]
            itc_claimed      = csv_data["itc_claimed"]
            line_net_tax     = csv_data["net_tax"]
            tax_code         = csv_data.get("tax_code", tax_code)
        else:
            taxable_sales, taxable_purchases = _query_gl_totals(period_start, period_end)

            if taxable_sales == 0 and taxable_purchases == 0:
                return TaskResult(
                    task_id=request.task_id,
                    task_type=TaskType.PREPARE_HST_RETURN,
                    agent_id=self.agent_id,
                    status=EventStatus.FAILURE,
                    error=f"No GL or bank transaction data found for period {return_period}",
                )

            # --- 3. Compute CRA GST34 line amounts ---
            tax_collected = (taxable_sales     * tax_rate).quantize(Decimal("0.01"))
            itc_claimed   = (taxable_purchases * tax_rate).quantize(Decimal("0.01"))
            line_net_tax  = tax_collected - itc_claimed

        line = HSTReturnLine(
            return_period=return_period,
            period_start=period_start,
            period_end=period_end,
            tax_code=tax_code,
            tax_description=_TAX_DESCRIPTIONS.get(tax_code, f"HST ({tax_code})"),
            taxable_sales=taxable_sales,
            tax_collected=tax_collected,
            taxable_purchases=taxable_purchases,
            itc_claimed=itc_claimed,
            line_net_tax=line_net_tax,
        )

        # --- 4. Write CSV when caller requests it ---
        if csv_output_path:
            _write_hst_csv(csv_output_path, line)

        # --- 5. Domain audit event ---
        filing_due = _filing_due_date(period_end)
        write(AuditRecord.ok(
            agent_id=self.agent_id,
            event_type=EventType.TAX_RETURN_PREPARED,
            action=TaskType.PREPARE_HST_RETURN.value,
            resource_type="hst_return",
            resource_id=return_period,
            session_id=request.session_id,
            metadata={
                "return_period":   return_period,
                "line_103":        str(tax_collected),
                "line_106":        str(itc_claimed),
                "line_109":        str(line_net_tax),
                "filing_due_date": filing_due.isoformat(),
            },
        ))

        # --- 6. Persist to BQ ---
        bq_table = ensure_table(DATASET, HST_TABLE, HSTReturnLine, **_HST_CFG)
        load_rows(DATASET, HST_TABLE, [line], session_id=request.session_id)

        summary = HSTReturnSummary(
            return_period=return_period,
            period_start=period_start,
            period_end=period_end,
            business_no=business_no,
            province=_PROVINCE_MAP.get(tax_code, "ON"),
            filing_due_date=filing_due,
            line_101_total_revenue=taxable_sales,
            line_103_hst_collected=tax_collected,
            line_106_itc_claimed=itc_claimed,
            line_109_net_tax=line_net_tax,
            is_refund=line_net_tax < Decimal("0"),
            tax_codes_applied=[tax_code],
            line_count=1,
            bq_lines_table=bq_table,
        )

        return TaskResult(
            task_id=request.task_id,
            task_type=TaskType.PREPARE_HST_RETURN,
            agent_id=self.agent_id,
            status=EventStatus.SUCCESS,
            output=summary.model_dump(mode="json"),
        )
