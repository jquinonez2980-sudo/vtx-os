"""
Agent base classes — TaskType, TaskRequest, TaskResult, AgentBase.

Every agent:
  1. Subclasses AgentBase and sets agent_id as a class attribute.
  2. Implements handle(request) → TaskResult with the core logic.
  3. Calls run(request) from callers — run() wraps handle() with
     millisecond timing and BQ audit logging automatically.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from models.base import AuditRecord, EventStatus, EventType, Severity


class TaskType(str, Enum):
    # Sage 50 ingestion
    INGEST_SAGE50_CSV = "INGEST_SAGE50_CSV"
    INGEST_SAGE50_ODBC = "INGEST_SAGE50_ODBC"        # Phase 2
    # Bookkeeping
    BOOKKEEPING_RUN = "BOOKKEEPING_RUN"
    RECONCILE_GL = "RECONCILE_GL"
    RECONCILE_BANK = "RECONCILE_BANK"
    # Tax — CRA
    PREPARE_HST_RETURN = "PREPARE_HST_RETURN"        # GST/HST remittance
    PREPARE_T2_RETURN = "PREPARE_T2_RETURN"          # Corporate income tax
    PREPARE_T4_SLIPS = "PREPARE_T4_SLIPS"            # Payroll
    PREPARE_T5_SLIPS = "PREPARE_T5_SLIPS"            # Investment income
    # Reporting
    PREPARE_FINANCIAL_STATEMENTS = "PREPARE_FINANCIAL_STATEMENTS"
    # Client communications
    SEND_CLIENT_EMAIL = "SEND_CLIENT_EMAIL"
    # Document processing
    PROCESS_DOCUMENT = "PROCESS_DOCUMENT"
    # RAG (Retrieval-Augmented Generation)
    INDEX_DOCUMENT = "INDEX_DOCUMENT"       # chunk + embed + store in vtx_rag
    RAG_QUERY      = "RAG_QUERY"            # embed query + VECTOR_SEARCH + return context
    # Journal entry posting to Sage 50
    POST_JOURNAL_ENTRIES = "POST_JOURNAL_ENTRIES"
    # Year-end
    YEAR_END_CLOSE = "YEAR_END_CLOSE"


class TaskRequest(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_type: TaskType
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    requested_by: str = ""                 # user email or upstream agent_id
    priority: int = 5                      # 1 = highest, 10 = lowest
    fiscal_year: int | None = None         # e.g. 2026
    fiscal_period: str | None = None       # e.g. "2026-Q1", "2026-03"
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskResult(BaseModel):
    task_id: str
    task_type: TaskType
    agent_id: str
    status: EventStatus
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    duration_ms: int | None = None
    audit_event_id: str | None = None      # links back to vtx_audit.audit_log

    @property
    def ok(self) -> bool:
        return self.status == EventStatus.SUCCESS


class AgentBase(ABC):
    """Abstract base for all VTX-OS agents.

    Subclasses must set agent_id and implement handle().
    Call run() — not handle() — from outside the agent.
    """

    agent_id: ClassVar[str]
    agent_version: ClassVar[str] = "0.1.0"

    @abstractmethod
    def handle(self, request: TaskRequest) -> TaskResult:
        """Core agent logic. Must return a TaskResult."""
        ...

    def run(self, request: TaskRequest) -> TaskResult:
        """Timed wrapper around handle() that writes BQ audit records."""
        from core.audit import write

        start_ns = time.monotonic_ns()

        start_record = AuditRecord(
            agent_id=self.agent_id,
            agent_version=self.agent_version,
            event_type=EventType.AGENT_START,
            severity=Severity.INFO,
            session_id=request.session_id,
            user_email=request.requested_by,
            resource_type="task",
            resource_id=request.task_id,
            action=request.task_type.value,
            status=EventStatus.PENDING,
            metadata={"task_type": request.task_type.value, "priority": request.priority},
        )
        write(start_record)

        try:
            result = self.handle(request)
        except Exception as exc:
            duration_ms = (time.monotonic_ns() - start_ns) // 1_000_000
            err_record = AuditRecord.fail(
                agent_id=self.agent_id,
                event_type=EventType.AGENT_ERROR,
                action=request.task_type.value,
                error=str(exc),
                error_code="UNHANDLED_EXCEPTION",
                resource_type="task",
                resource_id=request.task_id,
                session_id=request.session_id,
                metadata={"exception_type": type(exc).__name__},
            )
            err_record.duration_ms = duration_ms
            write(err_record)
            return TaskResult(
                task_id=request.task_id,
                task_type=request.task_type,
                agent_id=self.agent_id,
                status=EventStatus.FAILURE,
                error=str(exc),
                duration_ms=duration_ms,
                audit_event_id=err_record.event_id,
            )

        duration_ms = (time.monotonic_ns() - start_ns) // 1_000_000
        result.duration_ms = duration_ms

        event_type = EventType.AGENT_COMPLETE if result.ok else EventType.AGENT_ERROR
        severity = Severity.INFO if result.ok else Severity.ERROR
        done_record = AuditRecord(
            agent_id=self.agent_id,
            agent_version=self.agent_version,
            event_type=event_type,
            severity=severity,
            session_id=request.session_id,
            user_email=request.requested_by,
            resource_type="task",
            resource_id=request.task_id,
            action=request.task_type.value,
            status=result.status,
            duration_ms=duration_ms,
            error_message=result.error,
            metadata=result.output or None,
        )
        write(done_record)
        result.audit_event_id = done_record.event_id
        return result
