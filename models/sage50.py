"""
Pydantic row models for Sage 50 Canada CSV exports.

Each model maps the actual column headers Sage 50 produces (via Field aliases)
to clean Python field names. Parse a CSV row with:

    row = GLTransaction.from_csv(csv_dict_reader_row)

All monetary fields use Decimal — never float — for CRA-accurate arithmetic.
Dates are normalized to Python date objects. Province codes follow ISO 3166-2:CA.
"""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _dec(value: Any) -> Decimal:
    """Parse a Sage 50 numeric string (may contain $ and commas) to Decimal."""
    if isinstance(value, Decimal):
        return value
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    if not cleaned or cleaned == "-":
        return Decimal("0")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return Decimal("0")


def _date(value: Any) -> date | None:
    """Parse common Sage 50 date formats: MM/DD/YYYY, YYYY-MM-DD, DD-Mon-YYYY."""
    if not value or str(value).strip() == "":
        return None
    s = str(value).strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%d-%b-%Y", "%m-%d-%Y"):
        try:
            return date.fromisoformat(s) if fmt == "%Y-%m-%d" else __import__("datetime").datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


class _S50Base(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    @classmethod
    def from_csv(cls, row: dict[str, str]) -> "Any":
        """Construct from a csv.DictReader row (handles extra/unknown columns)."""
        known = {f.alias or name for name, f in cls.model_fields.items()}
        filtered = {k: v for k, v in row.items() if k in known}
        return cls.model_validate(filtered)

    @classmethod
    def iter_csv_file(cls, path: str | Path) -> Iterator["Any"]:
        """Yield one model instance per data row in a Sage 50 CSV export file."""
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                yield cls.from_csv(row)


# ---------------------------------------------------------------------------
# General Ledger Transactions
# Sage 50 export: Reports → Financials → General Ledger → Save as CSV
# ---------------------------------------------------------------------------

class GLTransaction(_S50Base):
    transaction_date: date | None = Field(None, alias="Date")
    journal_no: str = Field("", alias="Journal No.")
    source: str = Field("", alias="Source")
    account_no: str = Field("", alias="Account No.")
    account_name: str = Field("", alias="Account Description")
    debit: Decimal = Field(Decimal("0"), alias="Debit")
    credit: Decimal = Field(Decimal("0"), alias="Credit")
    description: str = Field("", alias="Comment")
    job_no: str | None = Field(None, alias="Job No.")
    division: str | None = Field(None, alias="Division")

    @field_validator("debit", "credit", mode="before")
    @classmethod
    def parse_amount(cls, v: Any) -> Decimal:
        return _dec(v)

    @field_validator("transaction_date", mode="before")
    @classmethod
    def parse_date(cls, v: Any) -> date | None:
        return _date(v)

    @property
    def net_amount(self) -> Decimal:
        return self.debit - self.credit


# ---------------------------------------------------------------------------
# Accounts Receivable — Invoices
# Sage 50: Customers → Sales Invoice List → Save as CSV
# ---------------------------------------------------------------------------

class ARInvoice(_S50Base):
    invoice_no: str = Field("", alias="Invoice No.")
    invoice_date: date | None = Field(None, alias="Date")
    customer_no: str = Field("", alias="Customer No.")
    customer_name: str = Field("", alias="Customer Name")
    subtotal: Decimal = Field(Decimal("0"), alias="Amount")
    tax_amount: Decimal = Field(Decimal("0"), alias="Tax Amount")
    total: Decimal = Field(Decimal("0"), alias="Amount Including Tax")
    due_date: date | None = Field(None, alias="Due Date")
    paid: bool = Field(False, alias="Paid")
    payment_date: date | None = Field(None, alias="Payment Date")
    job_no: str | None = Field(None, alias="Job No.")

    @field_validator("subtotal", "tax_amount", "total", mode="before")
    @classmethod
    def parse_amount(cls, v: Any) -> Decimal:
        return _dec(v)

    @field_validator("invoice_date", "due_date", "payment_date", mode="before")
    @classmethod
    def parse_date(cls, v: Any) -> date | None:
        return _date(v)

    @field_validator("paid", mode="before")
    @classmethod
    def parse_bool(cls, v: Any) -> bool:
        return str(v).strip().upper() in ("YES", "TRUE", "1", "Y", "PAID")


# ---------------------------------------------------------------------------
# Accounts Payable — Bills / Vendor Invoices
# Sage 50: Vendors → Purchase Invoice List → Save as CSV
# ---------------------------------------------------------------------------

class APBill(_S50Base):
    bill_no: str = Field("", alias="Invoice No.")
    bill_date: date | None = Field(None, alias="Date")
    vendor_no: str = Field("", alias="Vendor No.")
    vendor_name: str = Field("", alias="Vendor Name")
    subtotal: Decimal = Field(Decimal("0"), alias="Amount")
    tax_amount: Decimal = Field(Decimal("0"), alias="Tax Amount")
    total: Decimal = Field(Decimal("0"), alias="Amount Including Tax")
    due_date: date | None = Field(None, alias="Due Date")
    paid: bool = Field(False, alias="Paid")
    payment_date: date | None = Field(None, alias="Payment Date")

    @field_validator("subtotal", "tax_amount", "total", mode="before")
    @classmethod
    def parse_amount(cls, v: Any) -> Decimal:
        return _dec(v)

    @field_validator("bill_date", "due_date", "payment_date", mode="before")
    @classmethod
    def parse_date(cls, v: Any) -> date | None:
        return _date(v)

    @field_validator("paid", mode="before")
    @classmethod
    def parse_bool(cls, v: Any) -> bool:
        return str(v).strip().upper() in ("YES", "TRUE", "1", "Y", "PAID")


# ---------------------------------------------------------------------------
# Chart of Accounts
# Sage 50: Setup → Chart of Accounts → Save as CSV
# ---------------------------------------------------------------------------

class ChartOfAccountsEntry(_S50Base):
    account_no: str = Field("", alias="Account No.")
    account_name: str = Field("", alias="Account Description")
    account_type: str = Field("", alias="Account Type")
    account_class: str | None = Field(None, alias="Account Class")
    gifi_code: str | None = Field(None, alias="GIFI Code")  # CRA GIFI mapping
    balance: Decimal = Field(Decimal("0"), alias="Balance")
    is_active: bool = Field(True, alias="Active")

    @field_validator("balance", mode="before")
    @classmethod
    def parse_amount(cls, v: Any) -> Decimal:
        return _dec(v)

    @field_validator("is_active", mode="before")
    @classmethod
    def parse_bool(cls, v: Any) -> bool:
        return str(v).strip().upper() not in ("NO", "FALSE", "0", "N", "INACTIVE")


# ---------------------------------------------------------------------------
# Customers
# Sage 50: Customers → Customer List → Save as CSV
# ---------------------------------------------------------------------------

class Customer(_S50Base):
    customer_no: str = Field("", alias="Customer No.")
    name: str = Field("", alias="Contact Name")
    company: str | None = Field(None, alias="Company Name")
    address1: str | None = Field(None, alias="Address 1")
    address2: str | None = Field(None, alias="Address 2")
    city: str | None = Field(None, alias="City")
    province: str | None = Field(None, alias="Province")  # ON, QC, BC, …
    postal_code: str | None = Field(None, alias="Postal Code")
    phone: str | None = Field(None, alias="Phone 1")
    email: str | None = Field(None, alias="E-mail")
    credit_limit: Decimal = Field(Decimal("0"), alias="Credit Limit")
    balance: Decimal = Field(Decimal("0"), alias="Balance")
    tax_code: str | None = Field(None, alias="Tax Code")  # HST, GST, etc.
    is_active: bool = Field(True, alias="Active")

    @field_validator("credit_limit", "balance", mode="before")
    @classmethod
    def parse_amount(cls, v: Any) -> Decimal:
        return _dec(v)

    @field_validator("is_active", mode="before")
    @classmethod
    def parse_bool(cls, v: Any) -> bool:
        return str(v).strip().upper() not in ("NO", "FALSE", "0", "N", "INACTIVE")


# ---------------------------------------------------------------------------
# Vendors
# Sage 50: Vendors → Vendor List → Save as CSV
# ---------------------------------------------------------------------------

class Vendor(_S50Base):
    vendor_no: str = Field("", alias="Vendor No.")
    name: str = Field("", alias="Contact Name")
    company: str | None = Field(None, alias="Company Name")
    address1: str | None = Field(None, alias="Address 1")
    address2: str | None = Field(None, alias="Address 2")
    city: str | None = Field(None, alias="City")
    province: str | None = Field(None, alias="Province")
    postal_code: str | None = Field(None, alias="Postal Code")
    phone: str | None = Field(None, alias="Phone 1")
    email: str | None = Field(None, alias="E-mail")
    balance: Decimal = Field(Decimal("0"), alias="Balance")
    tax_code: str | None = Field(None, alias="Tax Code")
    business_no: str | None = Field(None, alias="Business No.")  # CRA BN
    is_active: bool = Field(True, alias="Active")

    @field_validator("balance", mode="before")
    @classmethod
    def parse_amount(cls, v: Any) -> Decimal:
        return _dec(v)

    @field_validator("is_active", mode="before")
    @classmethod
    def parse_bool(cls, v: Any) -> bool:
        return str(v).strip().upper() not in ("NO", "FALSE", "0", "N", "INACTIVE")


# ---------------------------------------------------------------------------
# Tax Summary — GST/HST/QST
# Sage 50: Reports → Tax → Sales Tax Summary → Save as CSV
# ---------------------------------------------------------------------------

class TaxSummary(_S50Base):
    period_start: date | None = Field(None, alias="Period Start")
    period_end: date | None = Field(None, alias="Period End")
    tax_code: str = Field("", alias="Tax Code")       # e.g. HST ON, GST, QST
    description: str = Field("", alias="Description")
    taxable_sales: Decimal = Field(Decimal("0"), alias="Taxable Sales")
    tax_collected: Decimal = Field(Decimal("0"), alias="Tax Collected")  # Line 103
    taxable_purchases: Decimal = Field(Decimal("0"), alias="Taxable Purchases")
    itc_claimed: Decimal = Field(Decimal("0"), alias="Input Tax Credits")  # Line 106
    net_tax: Decimal = Field(Decimal("0"), alias="Net Tax")               # Line 109

    @field_validator(
        "taxable_sales", "tax_collected", "taxable_purchases", "itc_claimed", "net_tax",
        mode="before",
    )
    @classmethod
    def parse_amount(cls, v: Any) -> Decimal:
        return _dec(v)

    @field_validator("period_start", "period_end", mode="before")
    @classmethod
    def parse_date(cls, v: Any) -> date | None:
        return _date(v)


# ---------------------------------------------------------------------------
# Payroll
# Sage 50: Reports → Payroll → Payroll Summary → Save as CSV
# ---------------------------------------------------------------------------

class PayrollEntry(_S50Base):
    employee_no: str = Field("", alias="Employee No.")
    employee_name: str = Field("", alias="Employee Name")
    pay_period_start: date | None = Field(None, alias="Period Start")
    pay_period_end: date | None = Field(None, alias="Period End")
    pay_date: date | None = Field(None, alias="Pay Date")
    gross_pay: Decimal = Field(Decimal("0"), alias="Gross Pay")
    cpp_employee: Decimal = Field(Decimal("0"), alias="CPP Employee")
    ei_employee: Decimal = Field(Decimal("0"), alias="EI Employee")
    federal_tax: Decimal = Field(Decimal("0"), alias="Federal Tax")
    provincial_tax: Decimal = Field(Decimal("0"), alias="Provincial Tax")
    other_deductions: Decimal = Field(Decimal("0"), alias="Other Deductions")
    net_pay: Decimal = Field(Decimal("0"), alias="Net Pay")
    cpp_employer: Decimal = Field(Decimal("0"), alias="CPP Employer")
    ei_employer: Decimal = Field(Decimal("0"), alias="EI Employer")
    province: str | None = Field(None, alias="Province")

    @field_validator(
        "gross_pay", "cpp_employee", "ei_employee", "federal_tax", "provincial_tax",
        "other_deductions", "net_pay", "cpp_employer", "ei_employer",
        mode="before",
    )
    @classmethod
    def parse_amount(cls, v: Any) -> Decimal:
        return _dec(v)

    @field_validator("pay_period_start", "pay_period_end", "pay_date", mode="before")
    @classmethod
    def parse_date(cls, v: Any) -> date | None:
        return _date(v)

    @property
    def total_employer_cost(self) -> Decimal:
        return self.gross_pay + self.cpp_employer + self.ei_employer


# ---------------------------------------------------------------------------
# Inventory
# Sage 50: Inventory & Services → Item List → Save as CSV
# ---------------------------------------------------------------------------

class InventoryItem(_S50Base):
    item_no: str = Field("", alias="Item No.")
    description: str = Field("", alias="Description")
    unit: str | None = Field(None, alias="Unit")
    qty_on_hand: Decimal = Field(Decimal("0"), alias="Quantity on Hand")
    qty_on_order: Decimal = Field(Decimal("0"), alias="Quantity on Order")
    unit_cost: Decimal = Field(Decimal("0"), alias="Average Cost")
    selling_price: Decimal = Field(Decimal("0"), alias="Regular Price")
    total_value: Decimal = Field(Decimal("0"), alias="Total Cost")
    account_no: str | None = Field(None, alias="Asset Account")
    tax_code: str | None = Field(None, alias="Tax Code")
    is_active: bool = Field(True, alias="Active")

    @field_validator(
        "qty_on_hand", "qty_on_order", "unit_cost", "selling_price", "total_value",
        mode="before",
    )
    @classmethod
    def parse_amount(cls, v: Any) -> Decimal:
        return _dec(v)

    @field_validator("is_active", mode="before")
    @classmethod
    def parse_bool(cls, v: Any) -> bool:
        return str(v).strip().upper() not in ("NO", "FALSE", "0", "N", "INACTIVE")


# ---------------------------------------------------------------------------
# Bank Reconciliation
# Sage 50: Banking → Reconciliation & Deposits → Reconciliation Report → Save as CSV
# ---------------------------------------------------------------------------

class BankReconciliation(_S50Base):
    account_no: str = Field("", alias="Account No.")
    account_name: str = Field("", alias="Account Name")
    statement_date: date | None = Field(None, alias="Statement Date")
    statement_balance: Decimal = Field(Decimal("0"), alias="Statement Balance")
    outstanding_deposits: Decimal = Field(Decimal("0"), alias="Outstanding Deposits")
    outstanding_cheques: Decimal = Field(Decimal("0"), alias="Outstanding Cheques")
    book_balance: Decimal = Field(Decimal("0"), alias="Book Balance")
    difference: Decimal = Field(Decimal("0"), alias="Difference")

    @field_validator(
        "statement_balance", "outstanding_deposits", "outstanding_cheques",
        "book_balance", "difference",
        mode="before",
    )
    @classmethod
    def parse_amount(cls, v: Any) -> Decimal:
        return _dec(v)

    @field_validator("statement_date", mode="before")
    @classmethod
    def parse_date(cls, v: Any) -> date | None:
        return _date(v)

    @property
    def is_reconciled(self) -> bool:
        return self.difference == Decimal("0")
