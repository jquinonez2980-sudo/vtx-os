"""
Approval queue models.

ApprovalItem is persisted to vtx_accounting.approval_queue via core/bq_loader.
ApprovalStatus drives the queue lifecycle: PENDING → APPROVED | REJECTED | ESCALATED.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ApprovalStatus(str, Enum):
    PENDING   = "PENDING"
    APPROVED  = "APPROVED"
    REJECTED  = "REJECTED"
    ESCALATED = "ESCALATED"
    POSTED    = "POSTED"     # set by the local posting agent after Sage 50 accepts the entry


class ApprovalItem(BaseModel):
    model_config = ConfigDict(extra="ignore")   # tolerate extra BQ tracking cols on read-back

    queue_id:         str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at:       datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    item_type:        str = "bank_transaction"
    item_id:          str                       # txn_id from CategorizedTransaction
    status:           ApprovalStatus = ApprovalStatus.PENDING
    session_id:       str = ""
    period:           str = ""                  # "YYYY-MM"
    bank_code:        str = ""
    account_no:       str = ""
    txn_date:         date
    description:      str
    amount:           Decimal
    suggested_gl_no:  str
    suggested_gl_name: str
    confidence:       float = 0.0
    priority:         int = 5
    # Set on review
    final_gl_no:      str | None = None
    reviewer_email:   str | None = None
    reviewed_at:      datetime | None = None
    review_note:      str | None = None

    @classmethod
    def from_categorized(
        cls,
        txn,                    # CategorizedTransaction
        session_id: str = "",
        period: str = "",
    ) -> "ApprovalItem":
        return cls(
            item_id=txn.txn_id,
            session_id=session_id,
            period=period,
            bank_code=txn.bank_code.value,
            account_no=txn.account_no,
            txn_date=txn.txn_date,
            description=txn.description,
            amount=txn.amount,
            suggested_gl_no=txn.gl_account_no or "9999",
            suggested_gl_name=txn.gl_account_name or "Unclassified",
            confidence=txn.confidence,
        )
