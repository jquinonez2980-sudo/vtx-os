"""
Sage 50 Canada ODBC reader.

Connects via pyodbc using the connection string from Secret Manager
(vtx-sage50-odbc-conn). Returns rows as Pydantic model instances ready
for BigQuery loading via core.bq_loader.

Prerequisites on the Windows machine running this code:
  - Sage 50 installed (provides the ODBC driver)
  - Windows ODBC Data Source configured, OR use a DSN-less connection string
  - Connection string format (either):
      DSN-based:  DSN=Sage50Company;UID=sysadmin;PWD=yourpassword
      DSN-less:   Driver={Sage 50 ODBC Driver};
                  CompanyDatabase=C:\\path\\to\\company.sai;
                  UID=sysadmin;PWD=yourpassword

Table names below are verified against Sage 50 Canada Premium 2024.
Call discover_tables() to list the actual tables in your installation —
names can vary between Pro/Premium/Quantum and language settings.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date
from typing import Iterator

import pyodbc

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

# ---------------------------------------------------------------------------
# Table name constants — adjust if your Sage 50 version uses different names.
# Use discover_tables() to list what your ODBC driver actually exposes.
# ---------------------------------------------------------------------------
_T_GL        = "GeneralLedger"      # GL transactions (alt: "Transaction", "Journal")
_T_AR        = "ARInvoice"          # AR invoices     (alt: "SaleInvoice", "Invoice")
_T_AP        = "APInvoice"          # AP bills        (alt: "PurchaseInvoice")
_T_COA       = "Account"            # Chart of accounts
_T_CUSTOMER  = "Customer"
_T_VENDOR    = "Vendor"
_T_ITEM      = "Item"               # Inventory       (alt: "InventoryItem")
_T_PAYROLL   = "PayrollJournal"     # Payroll entries (alt: "Payroll")
_T_BANK_REC  = "BankReconciliation"
_T_TAX       = "TaxSummary"         # Tax summary     (alt: "Tax", "TaxCode")

# ODBC date literal — Sage 50 accepts standard ODBC escape syntax
def _dlit(d: date) -> str:
    return f"{{d '{d.isoformat()}'}}"


@contextmanager
def _connect(conn_str: str | None = None) -> Iterator[pyodbc.Connection]:
    """Context-managed ODBC connection. Reads conn string from Secret Manager
    if not supplied directly (useful for tests/local override)."""
    if conn_str is None:
        from core.secrets import get_sage50_odbc_conn
        conn_str = get_sage50_odbc_conn()

    conn = pyodbc.connect(conn_str, autocommit=True, timeout=30)
    try:
        yield conn
    finally:
        conn.close()


def _row_to_dict(cursor: pyodbc.Cursor, row: pyodbc.Row) -> dict[str, str]:
    """Convert a pyodbc Row to a plain dict keyed by column name."""
    cols = [d[0] for d in cursor.description]
    return {col: (str(val) if val is not None else "") for col, val in zip(cols, row)}


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

def discover_tables(conn_str: str | None = None) -> list[str]:
    """List all table/view names exposed by the Sage 50 ODBC driver."""
    with _connect(conn_str) as conn:
        cursor = conn.cursor()
        return sorted(t.table_name for t in cursor.tables())


def test_connection(conn_str: str | None = None) -> bool:
    """Return True if the ODBC connection succeeds."""
    try:
        with _connect(conn_str) as conn:
            conn.cursor().execute("SELECT 1")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Fetch functions — each returns a list of typed Pydantic model instances
# ---------------------------------------------------------------------------

def fetch_gl_transactions(
    start_date: date,
    end_date: date,
    conn_str: str | None = None,
) -> list[GLTransaction]:
    sql = f"""
        SELECT Date, [Journal No.], Source, [Account No.], [Account Description],
               Debit, Credit, Comment, [Job No.], Division
        FROM {_T_GL}
        WHERE Date >= {_dlit(start_date)} AND Date <= {_dlit(end_date)}
        ORDER BY Date, [Journal No.]
    """
    with _connect(conn_str) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        return [GLTransaction.from_csv(_row_to_dict(cur, row)) for row in cur.fetchall()]


def fetch_ar_invoices(
    start_date: date,
    end_date: date,
    conn_str: str | None = None,
) -> list[ARInvoice]:
    sql = f"""
        SELECT [Invoice No.], Date, [Customer No.], [Customer Name],
               Amount, [Tax Amount], [Amount Including Tax],
               [Due Date], Paid, [Payment Date], [Job No.]
        FROM {_T_AR}
        WHERE Date >= {_dlit(start_date)} AND Date <= {_dlit(end_date)}
        ORDER BY Date, [Invoice No.]
    """
    with _connect(conn_str) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        return [ARInvoice.from_csv(_row_to_dict(cur, row)) for row in cur.fetchall()]


def fetch_ap_bills(
    start_date: date,
    end_date: date,
    conn_str: str | None = None,
) -> list[APBill]:
    sql = f"""
        SELECT [Invoice No.], Date, [Vendor No.], [Vendor Name],
               Amount, [Tax Amount], [Amount Including Tax],
               [Due Date], Paid, [Payment Date]
        FROM {_T_AP}
        WHERE Date >= {_dlit(start_date)} AND Date <= {_dlit(end_date)}
        ORDER BY Date, [Invoice No.]
    """
    with _connect(conn_str) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        return [APBill.from_csv(_row_to_dict(cur, row)) for row in cur.fetchall()]


def fetch_chart_of_accounts(conn_str: str | None = None) -> list[ChartOfAccountsEntry]:
    sql = f"""
        SELECT [Account No.], [Account Description], [Account Type],
               [Account Class], [GIFI Code], Balance, Active
        FROM {_T_COA}
        ORDER BY [Account No.]
    """
    with _connect(conn_str) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        return [ChartOfAccountsEntry.from_csv(_row_to_dict(cur, row)) for row in cur.fetchall()]


def fetch_customers(conn_str: str | None = None) -> list[Customer]:
    sql = f"""
        SELECT [Customer No.], [Contact Name], [Company Name],
               [Address 1], [Address 2], City, Province, [Postal Code],
               [Phone 1], [E-mail], [Credit Limit], Balance, [Tax Code], Active
        FROM {_T_CUSTOMER}
        ORDER BY [Customer No.]
    """
    with _connect(conn_str) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        return [Customer.from_csv(_row_to_dict(cur, row)) for row in cur.fetchall()]


def fetch_vendors(conn_str: str | None = None) -> list[Vendor]:
    sql = f"""
        SELECT [Vendor No.], [Contact Name], [Company Name],
               [Address 1], [Address 2], City, Province, [Postal Code],
               [Phone 1], [E-mail], Balance, [Tax Code], [Business No.], Active
        FROM {_T_VENDOR}
        ORDER BY [Vendor No.]
    """
    with _connect(conn_str) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        return [Vendor.from_csv(_row_to_dict(cur, row)) for row in cur.fetchall()]


def fetch_inventory(conn_str: str | None = None) -> list[InventoryItem]:
    sql = f"""
        SELECT [Item No.], Description, Unit,
               [Quantity on Hand], [Quantity on Order],
               [Average Cost], [Regular Price], [Total Cost],
               [Asset Account], [Tax Code], Active
        FROM {_T_ITEM}
        ORDER BY [Item No.]
    """
    with _connect(conn_str) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        return [InventoryItem.from_csv(_row_to_dict(cur, row)) for row in cur.fetchall()]


def fetch_payroll(
    start_date: date,
    end_date: date,
    conn_str: str | None = None,
) -> list[PayrollEntry]:
    sql = f"""
        SELECT [Employee No.], [Employee Name],
               [Period Start], [Period End], [Pay Date],
               [Gross Pay], [CPP Employee], [EI Employee],
               [Federal Tax], [Provincial Tax], [Other Deductions],
               [Net Pay], [CPP Employer], [EI Employer], Province
        FROM {_T_PAYROLL}
        WHERE [Pay Date] >= {_dlit(start_date)} AND [Pay Date] <= {_dlit(end_date)}
        ORDER BY [Pay Date], [Employee No.]
    """
    with _connect(conn_str) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        return [PayrollEntry.from_csv(_row_to_dict(cur, row)) for row in cur.fetchall()]


def fetch_tax_summary(
    start_date: date,
    end_date: date,
    conn_str: str | None = None,
) -> list[TaxSummary]:
    sql = f"""
        SELECT [Period Start], [Period End], [Tax Code], Description,
               [Taxable Sales], [Tax Collected],
               [Taxable Purchases], [Input Tax Credits], [Net Tax]
        FROM {_T_TAX}
        WHERE [Period End] >= {_dlit(start_date)} AND [Period Start] <= {_dlit(end_date)}
        ORDER BY [Period Start], [Tax Code]
    """
    with _connect(conn_str) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        return [TaxSummary.from_csv(_row_to_dict(cur, row)) for row in cur.fetchall()]


def fetch_bank_reconciliation(
    as_of_date: date | None = None,
    conn_str: str | None = None,
) -> list[BankReconciliation]:
    if as_of_date:
        where = f"WHERE [Statement Date] <= {_dlit(as_of_date)}"
    else:
        where = ""
    sql = f"""
        SELECT [Account No.], [Account Name], [Statement Date],
               [Statement Balance], [Outstanding Deposits],
               [Outstanding Cheques], [Book Balance], Difference
        FROM {_T_BANK_REC}
        {where}
        ORDER BY [Account No.], [Statement Date]
    """
    with _connect(conn_str) as conn:
        cur = conn.cursor()
        cur.execute(sql)
        return [BankReconciliation.from_csv(_row_to_dict(cur, row)) for row in cur.fetchall()]
