"""
models/hst_return.py
Data models for HST/GST return preparation.

HSTReturnLine     one BQ row per tax code per reporting period
HSTReturnSummary  TaskResult.output for PrepareHSTReturnAgent
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class HSTReturnLine(BaseModel):
    """One row in vtx_accounting.hst_returns — one tax code per filing period."""
    line_id:            str     = Field(default_factory=lambda: str(uuid.uuid4()))
    return_period:      str                 # YYYY-MM
    period_start:       date
    period_end:         date
    tax_code:           str                 # e.g. "H" (Ontario HST), "G" (GST)
    tax_description:    str
    taxable_sales:      Decimal             # feeds GST34 line 101
    tax_collected:      Decimal             # GST34 line 103
    taxable_purchases:  Decimal
    itc_claimed:        Decimal             # GST34 line 106
    line_net_tax:       Decimal             # = tax_collected - itc_claimed (line 109 component)


class HSTReturnSummary(BaseModel):
    """Returned by PrepareHSTReturnAgent as TaskResult.output."""
    return_period:          str
    period_start:           date
    period_end:             date
    business_no:            Optional[str]   = None   # CRA Business Number (BN)
    province:               str
    filing_due_date:        date
    line_101_total_revenue: Decimal         # total taxable sales across all tax codes
    line_103_hst_collected: Decimal         # total HST/GST/QST collected
    line_106_itc_claimed:   Decimal         # total input tax credits claimed
    line_109_net_tax:       Decimal         # line_103 - line_106 (positive = owing)
    is_refund:              bool            # True if line_109 < 0
    tax_codes_applied:      list[str]
    line_count:             int
    bq_lines_table:         str = ""
