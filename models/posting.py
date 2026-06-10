"""
Posting pipeline models.

PostRequest is persisted to vtx_accounting.post_requests. The dashboard inserts
QUEUED rows (via DML, never streaming — rows must be UPDATE-able immediately);
the local posting agent (scripts/posting_agent.py) polls for QUEUED, marks
RUNNING, posts to Sage 50 via the bridge, then marks DONE or FAILED.

Lifecycle: QUEUED → RUNNING → DONE | FAILED
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class PostRequestStatus(str, Enum):
    QUEUED  = "QUEUED"
    RUNNING = "RUNNING"
    DONE    = "DONE"
    FAILED  = "FAILED"


class PostRequest(BaseModel):
    request_id:   str = Field(default_factory=lambda: str(uuid.uuid4()))
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    requested_by: str = ""               # reviewer email from the dashboard JWT/key
    client_id:    str = ""
    account_no:   str                    # masked, e.g. xxxx4733 — matches BQ data
    period:       str = ""               # "YYYY-MM"; empty = all unposted
    status:       PostRequestStatus = PostRequestStatus.QUEUED
    # Set by the posting agent
    started_at:   datetime | None = None
    completed_at: datetime | None = None
    posted:       int = 0
    skipped:      int = 0
    errors:       int = 0
    result_note:  str = ""
