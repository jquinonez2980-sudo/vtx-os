"""
models/reconciliation.py
Data models for GL reconciliation.

GLEntry             one bank-account line from a Sage 50 GL export
ReconciliationItem  one row written to vtx_accounting.gl_reconciliation (BQ)
ReconciliationSummary agent output payload (TaskResult.output)
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MatchStatus(str, Enum):
    MATCHED       = "MATCHED"        # bank txn paired with GL entry
    UNMATCHED_BANK = "UNMATCHED_BANK"  # in bank statement, no GL entry found
    UNMATCHED_GL  = "UNMATCHED_GL"   # in GL, no bank transaction found


class GLEntry(BaseModel):
    """One line from the GL export that touches the bank account (e.g. 1060)."""
    entry_date:   date
    source_no:    str
    account_no:   str
    account_name: str
    description:  str
    debit:        Decimal
    credit:       Decimal

    @property
    def gl_net_amount(self) -> Decimal:
        """Net effect on this account: positive = debit (asset up = money in),
        negative = credit (asset down = money out).

        Mirrors bank_parser sign convention: positive = deposit, negative = withdrawal.
        For account 1060:
          - Payment (bank withdrawal -X): credit=X, debit=0  → net = -X  ✓
          - Receipt (bank deposit   +X): debit=X,  credit=0 → net = +X  ✓
        """
        return self.debit - self.credit


class ReconciliationItem(BaseModel):
    """One row in vtx_accounting.gl_reconciliation."""
    reconciliation_id: str  = Field(default_factory=lambda: str(uuid.uuid4()))
    period:            str
    account_no:        str   # bank account identifier (e.g. "xxxx5443")
    gl_bank_account:   str   # GL account number (e.g. "1060")
    match_status:      MatchStatus
    # Always set — used as BQ partition field
    reconciliation_date: date

    # Bank side (None for UNMATCHED_GL)
    bank_txn_id:      Optional[str]     = None
    bank_date:        Optional[date]    = None
    bank_description: Optional[str]    = None
    bank_amount:      Optional[Decimal] = None

    # GL side (None for UNMATCHED_BANK)
    gl_source_no:     Optional[str]     = None
    gl_date:          Optional[date]    = None
    gl_description:   Optional[str]    = None
    gl_amount:        Optional[Decimal] = None

    # Match quality (set only for MATCHED)
    amount_diff:      Optional[Decimal] = None
    date_diff_days:   Optional[int]     = None


class ReconciliationSummary(BaseModel):
    """Returned by ReconcileGLAgent as TaskResult.output."""
    period:               str
    account_no:           str
    gl_bank_account:      str
    bank_txn_count:       int
    gl_entry_count:       int
    matched_count:        int
    unmatched_bank_count: int
    unmatched_gl_count:   int
    total_bank_deposits:  Decimal
    total_bank_withdrawals: Decimal
    total_gl_debits:      Decimal    # debits to 1060 = deposits
    total_gl_credits:     Decimal    # credits to 1060 = withdrawals
    bank_net:             Decimal    # deposits - withdrawals (bank perspective)
    gl_net:               Decimal    # debits - credits on 1060 (GL perspective)
    net_difference:       Decimal    # gl_net - bank_net (0 = fully reconciled)
    is_reconciled:        bool       # True only when unmatched counts = 0
    bq_results_table:     str
