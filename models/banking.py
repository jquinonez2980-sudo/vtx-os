"""
Banking data models — bank statement transactions, categorization, journal entry drafts.

BankTransaction        raw parsed row from any supported bank CSV
CategorizedTransaction extends BankTransaction with GL account mapping + confidence
JournalEntryDraft      balanced double-entry pair derived from a categorized transaction
BookkeepingSummary     agent output payload
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, computed_field


class BankCode(str, Enum):
    RBC         = "RBC"
    TD          = "TD"
    BMO         = "BMO"
    CIBC        = "CIBC"
    SCOTIABANK  = "SCOTIABANK"
    NATIONAL    = "NATIONAL"
    DESJARDINS  = "DESJARDINS"
    GENERIC     = "GENERIC"


class BankTransaction(BaseModel):
    txn_id:          str           # sha256[:20] of bank|account|date|desc|amount|row
    bank_code:       BankCode
    account_no:      str           # last-4 or masked identifier for BQ (never full number)
    txn_date:        date
    description:     str           # cleaned description
    raw_description: str           # verbatim from CSV
    amount:          Decimal       # positive = deposit/credit, negative = withdrawal/debit
    balance:         Decimal | None = None
    reference:       str | None = None   # cheque number or bank reference


class CategorizationRule(BaseModel):
    rule_id:         str
    pattern:         str           # regex matched against description (case-insensitive)
    gl_account_no:   str
    gl_account_name: str
    category:        str           # human label, e.g. "Bank Charges"
    priority:        int = 5       # lower = evaluated first
    is_regex:        bool = True


class CategorizedTransaction(BankTransaction):
    gl_account_no:   str | None = None
    gl_account_name: str | None = None
    category:        str | None = None
    confidence:      float = 0.0   # 0.0–1.0
    matched_rule_id: str | None = None
    needs_review:    bool = True   # True when confidence < threshold


class JournalEntryLine(BaseModel):
    account_no:   str
    account_name: str
    description:  str
    debit:        Decimal = Decimal("0")
    credit:       Decimal = Decimal("0")


class JournalEntryDraft(BaseModel):
    """Balanced double-entry pair for one bank transaction."""
    draft_id:      str
    entry_date:    date
    reference:     str             # links back to txn_id
    description:   str
    debit_line:    JournalEntryLine
    credit_line:   JournalEntryLine
    source_txn_id: str

    @property
    def is_balanced(self) -> bool:
        return (
            self.debit_line.debit == self.credit_line.credit
            and self.debit_line.credit == Decimal("0")
            and self.credit_line.debit == Decimal("0")
        )


class BookkeepingSummary(BaseModel):
    """Returned by BookkeepingAgent as TaskResult.output."""
    period:                  str
    bank_code:               str
    account_no:              str
    total_transactions:      int
    auto_categorized:        int   # confidence >= threshold
    needs_review:            int   # confidence < threshold
    total_deposits:          Decimal
    total_withdrawals:       Decimal
    net_movement:            Decimal
    bq_raw_table:            str
    bq_categorized_table:    str
    # Set after approval queue + notification steps
    queue_items_submitted:   int = 0
    chat_notified:           bool = False
