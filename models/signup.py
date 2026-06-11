"""
Early-access signup model — persisted to vtx_accounting.early_access_signups
by the PUBLIC /api/signup endpoint (landing page lead capture).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class EarlySignup(BaseModel):
    signup_id:  str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    name:       str = ""
    email:      str
    firm:       str = ""
    clients:    str = ""          # self-reported client count bracket, e.g. "11-50"
    source:     str = "landing"   # attribution
