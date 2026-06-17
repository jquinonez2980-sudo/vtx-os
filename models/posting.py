"""
models/posting.py

PostRequest — one BQ row in vtx_accounting.post_requests.
PostStatus  — lifecycle: QUEUED → CLAIMED → DONE | FAILED.

The Cloud Run dashboard cannot reach Sage 50 (Windows-only bridge on the
bookkeeping machine), so it enqueues PostRequest rows here.
scripts/posting_agent.py --watch polls for QUEUED rows, claims them, runs the
Sage 50 posting, and updates status to DONE or FAILED.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class PostStatus(str, Enum):
    QUEUED  = "QUEUED"    # written by dashboard; awaiting local agent
    CLAIMED = "CLAIMED"   # local agent locked it
    DONE    = "DONE"      # Sage 50 accepted the entries
    FAILED  = "FAILED"    # posting agent hit an error


class PostRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request_id:    str      = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at:    datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status:        PostStatus = PostStatus.QUEUED

    # Who triggered it (reviewer JWT email)
    requested_by:  str = ""

    # Which client / account / period
    client_id:     str = ""
    account_no:    str = ""
    period:        str = ""   # "YYYY-MM"

    # Set by the posting agent on completion
    posted_count:  int | None = None
    error_detail:  str | None = None
    claimed_at:    datetime | None = None
    completed_at:  datetime | None = None
