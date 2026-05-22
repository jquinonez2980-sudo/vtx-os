"""Core agent event and audit record models."""

from __future__ import annotations

import decimal
import json
import uuid
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class _SafeEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal, date, and datetime in metadata payloads."""
    def default(self, o: Any) -> Any:
        if isinstance(o, decimal.Decimal):
            return str(o)
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        return super().default(o)


class Severity(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class EventType(str, Enum):
    # Sage 50 CSV ingestion
    INGEST_START = "INGEST_START"
    INGEST_COMPLETE = "INGEST_COMPLETE"
    INGEST_FAILED = "INGEST_FAILED"
    # BigQuery load
    BQ_LOAD_START = "BQ_LOAD_START"
    BQ_LOAD_COMPLETE = "BQ_LOAD_COMPLETE"
    BQ_LOAD_FAILED = "BQ_LOAD_FAILED"
    # Agent lifecycle
    AGENT_START = "AGENT_START"
    AGENT_COMPLETE = "AGENT_COMPLETE"
    AGENT_ERROR = "AGENT_ERROR"
    # Orchestration
    TASK_CREATED = "TASK_CREATED"
    TASK_DELEGATED = "TASK_DELEGATED"
    TASK_COMPLETE = "TASK_COMPLETE"
    TASK_FAILED = "TASK_FAILED"
    # Document AI
    DOCUMENT_RECEIVED = "DOCUMENT_RECEIVED"
    DOCUMENT_PARSED = "DOCUMENT_PARSED"
    # Tax / CRA
    TAX_RETURN_PREPARED = "TAX_RETURN_PREPARED"
    CRA_SUBMISSION = "CRA_SUBMISSION"
    # Communications
    EMAIL_SENT = "EMAIL_SENT"
    EMAIL_RECEIVED = "EMAIL_RECEIVED"


class EventStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    PENDING = "PENDING"
    SKIPPED = "SKIPPED"
    PARTIAL = "PARTIAL"


class AgentEvent(BaseModel):
    """Lightweight event emitted by any agent during operation."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_id: str
    agent_version: str = "0.1.0"
    event_type: EventType
    severity: Severity = Severity.INFO
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    resource_type: str = ""
    resource_id: str = ""
    action: str = ""
    status: EventStatus = EventStatus.PENDING
    duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] | None = None

    def to_audit_record(self, user_email: str = "") -> "AuditRecord":
        return AuditRecord(**self.model_dump(), user_email=user_email)


class AuditRecord(AgentEvent):
    """Matches vtx_audit.audit_log BigQuery schema exactly.

    Call to_bq_row() before inserting via the BigQuery streaming API.
    """

    user_email: str = ""

    def to_bq_row(self) -> dict[str, Any]:
        row = self.model_dump()
        row["event_ts"] = self.event_ts.isoformat()
        row["event_type"] = self.event_type.value
        row["severity"] = self.severity.value
        row["status"] = self.status.value
        if self.metadata is not None:
            row["metadata"] = json.dumps(self.metadata, cls=_SafeEncoder)
        return row

    @classmethod
    def ok(
        cls,
        agent_id: str,
        event_type: EventType,
        action: str,
        *,
        resource_type: str = "",
        resource_id: str = "",
        duration_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> "AuditRecord":
        kwargs: dict[str, Any] = dict(
            agent_id=agent_id,
            event_type=event_type,
            action=action,
            status=EventStatus.SUCCESS,
            severity=Severity.INFO,
            resource_type=resource_type,
            resource_id=resource_id,
            duration_ms=duration_ms,
            metadata=metadata,
        )
        if session_id:
            kwargs["session_id"] = session_id
        return cls(**kwargs)

    @classmethod
    def fail(
        cls,
        agent_id: str,
        event_type: EventType,
        action: str,
        error: str,
        *,
        error_code: str = "UNSPECIFIED_ERROR",
        resource_type: str = "",
        resource_id: str = "",
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> "AuditRecord":
        kwargs: dict[str, Any] = dict(
            agent_id=agent_id,
            event_type=event_type,
            action=action,
            status=EventStatus.FAILURE,
            severity=Severity.ERROR,
            error_code=error_code,
            error_message=error,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata=metadata,
        )
        if session_id:
            kwargs["session_id"] = session_id
        return cls(**kwargs)
