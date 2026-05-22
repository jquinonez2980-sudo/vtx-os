"""
Sage50OdbcAgent — reads Sage 50 data via Sage50Bridge.exe and loads into BigQuery.

Handles TaskType.INGEST_SAGE50_ODBC.

Required payload keys:
    report_type  (str)            — one of the REPORT_* constants below

Optional payload keys:
    sai_file     (str)            — path to the .SAI file; falls back to
                                    env VTX_SAGE50_SAI or Secret Manager
                                    vtx-sage50-company-path
    sage50_user  (str)            — Sage 50 username (default: sysadmin)
    sage50_password (str)         — Sage 50 password; falls back to env
                                    VTX_SAGE50_PASSWORD or Secret Manager
                                    vtx-sage50-password
    start_date   (str, YYYY-MM-DD) — inclusive; required for date-filtered reports
    end_date     (str, YYYY-MM-DD) — inclusive; required for date-filtered reports
    fiscal_year  (int)            — echoed to BQ _session metadata
    fiscal_period (str)           — e.g. "2026-Q1"

Returns output keys:
    report_type  — echoed back
    row_count    — rows fetched from Sage 50
    bq_table     — fully-qualified BQ table written to
    start_date / end_date  (when applicable)

IMPORTANT: Sage 50 must be CLOSED before this agent runs. The Sage50Bridge SDK
opens the .SAI file exclusively; a running Sage 50 instance holds a file lock
and causes OpenDatabase to fail.
"""

from __future__ import annotations

from datetime import date

from agents.base import AgentBase, TaskRequest, TaskResult, TaskType
from core.bq_loader import ensure_table, load_rows
from models.base import EventStatus
from models.sage50 import (
    APBill,
    ARInvoice,
    BankReconciliation,
    ChartOfAccountsEntry,
    Customer,
    GLTransaction,
    InventoryItem,
    PayrollEntry,
    TaxSummary,
    Vendor,
)
from sage50.bridge_reader import (
    fetch_ap_bills,
    fetch_ar_invoices,
    fetch_bank_reconciliation,
    fetch_chart_of_accounts,
    fetch_customers,
    fetch_gl_transactions,
    fetch_inventory,
    fetch_payroll,
    fetch_tax_summary,
    fetch_vendors,
)

DATASET = "vtx_accounting"

# Maps report_type → (BQ table name, model class, fetch function, needs_dates)
_REPORT_REGISTRY: dict[str, tuple] = {
    "gl_transactions":     ("gl_transactions",    GLTransaction,        fetch_gl_transactions,  True),
    "ar_invoices":         ("ar_invoices",         ARInvoice,            fetch_ar_invoices,       True),
    "ap_bills":            ("ap_bills",            APBill,               fetch_ap_bills,          True),
    "chart_of_accounts":   ("chart_of_accounts",   ChartOfAccountsEntry, fetch_chart_of_accounts, False),
    "customers":           ("customers",           Customer,             fetch_customers,         False),
    "vendors":             ("vendors",             Vendor,               fetch_vendors,           False),
    "inventory":           ("inventory",           InventoryItem,        fetch_inventory,         False),
    "payroll":             ("payroll",             PayrollEntry,         fetch_payroll,           True),
    "tax_summary":         ("tax_summary",         TaxSummary,           fetch_tax_summary,       True),
    "bank_reconciliation": ("bank_reconciliation", BankReconciliation,   fetch_bank_reconciliation, False),
}

_TABLE_CONFIG: dict[str, dict] = {
    "gl_transactions":   {"partition_field": "transaction_date", "cluster_fields": ["account_no"]},
    "ar_invoices":       {"partition_field": "invoice_date",     "cluster_fields": ["customer_no"]},
    "ap_bills":          {"partition_field": "bill_date",        "cluster_fields": ["vendor_no"]},
    "payroll":           {"partition_field": "pay_date",         "cluster_fields": ["employee_no"]},
    "tax_summary":       {"partition_field": "period_start",     "cluster_fields": ["tax_code"]},
}


class Sage50OdbcAgent(AgentBase):
    agent_id = "sage50-odbc-agent"

    def handle(self, request: TaskRequest) -> TaskResult:
        payload = request.payload
        report_type = payload["report_type"]

        if report_type not in _REPORT_REGISTRY:
            raise ValueError(
                f"Unknown report_type '{report_type}'. "
                f"Valid: {sorted(_REPORT_REGISTRY)}"
            )

        table_name, model_class, fetch_fn, needs_dates = _REPORT_REGISTRY[report_type]
        cfg = _TABLE_CONFIG.get(report_type, {})

        # Bridge credentials — callers can pass explicitly or let bridge_reader
        # fall back to env vars / Secret Manager.
        creds = {
            "sai_file": payload.get("sai_file"),
            "user":     payload.get("sage50_user"),
            "password": payload.get("sage50_password"),
        }

        bq_table = ensure_table(DATASET, table_name, model_class, **cfg)

        if needs_dates:
            raw_start = payload.get("start_date")
            raw_end   = payload.get("end_date")
            start_date = date.fromisoformat(raw_start) if raw_start else None
            end_date   = date.fromisoformat(raw_end)   if raw_end   else None
            rows = fetch_fn(start_date, end_date, **creds)
        else:
            rows = fetch_fn(**creds)

        loaded = load_rows(DATASET, table_name, rows, session_id=request.session_id)

        output: dict = {
            "report_type": report_type,
            "row_count":   loaded,
            "bq_table":    bq_table,
        }
        if needs_dates and raw_start:
            output["start_date"] = raw_start
            output["end_date"]   = raw_end

        return TaskResult(
            task_id=request.task_id,
            task_type=TaskType.INGEST_SAGE50_ODBC,
            agent_id=self.agent_id,
            status=EventStatus.SUCCESS,
            output=output,
        )
