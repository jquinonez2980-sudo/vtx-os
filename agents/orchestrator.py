"""
OrchestratorAgent — top-level dispatcher for all VTX-OS tasks.

Routing:
    TaskType  →  registered AgentBase instance
    Unknown   →  immediate FAILURE result (no sub-agent crash)

Audit trail written to vtx_audit.audit_log for every step:
    TASK_CREATED  →  TASK_DELEGATED  →  TASK_COMPLETE | TASK_FAILED

Usage:
    from agents.orchestrator import OrchestratorAgent, TaskRequest, TaskType

    orch = OrchestratorAgent()
    result = orch.run(TaskRequest(
        task_type=TaskType.INGEST_SAGE50_CSV,
        requested_by="jquinonez2980@gmail.com",
        payload={"local_path": "C:\\\\exports\\\\gl.csv", "report_type": "gl_transactions"},
    ))
    print(result.ok, result.output)
"""

from __future__ import annotations

import time
from typing import ClassVar

from agents.a2a import A2AAgentServer, A2ATransport
from agents.base import AgentBase, TaskRequest, TaskResult, TaskType
from core.audit import write
from models.base import AuditRecord, EventStatus, EventType, Severity


def _a2a_to_task_result(
    a2a_task: "A2ATask",  # type: ignore[name-defined]  # noqa: F821
    request: TaskRequest,
    agent_id: str,
) -> TaskResult:
    """Unpack a completed A2ATask into a TaskResult the orchestrator can return."""
    from agents.a2a import A2ATask, A2ATaskState, A2ATextPart

    if a2a_task.status.state == A2ATaskState.COMPLETED and a2a_task.artifacts:
        tr_data = a2a_task.artifacts[0].get("task_result", {})
        return TaskResult(**tr_data)

    # Extract error text from the status message if present
    error = f"A2A task ended in state '{a2a_task.status.state.value}'"
    if a2a_task.status.message:
        for part in a2a_task.status.message.parts:
            if isinstance(part, A2ATextPart):
                error = part.text
                break

    return TaskResult(
        task_id=request.task_id,
        task_type=request.task_type,
        agent_id=agent_id,
        status=EventStatus.FAILURE,
        error=error,
    )


class OrchestratorAgent(AgentBase):
    agent_id = "orchestrator-agent"

    # Maps TaskType → AgentBase instance.
    # Populated by register() calls at the bottom of this module.
    _registry: ClassVar[dict[TaskType, AgentBase]] = {}

    @classmethod
    def register(cls, task_type: TaskType, agent: AgentBase) -> None:
        cls._registry[task_type] = agent
        A2ATransport.register(agent.agent_id, A2AAgentServer(agent))

    @classmethod
    def registered_types(cls) -> list[TaskType]:
        return list(cls._registry.keys())

    # ------------------------------------------------------------------
    # handle() is the inner logic; run() (from AgentBase) wraps it with
    # AGENT_START / AGENT_COMPLETE audit records and timing.
    # ------------------------------------------------------------------

    def handle(self, request: TaskRequest) -> TaskResult:
        # Log task creation before delegating
        created = AuditRecord.ok(
            agent_id=self.agent_id,
            event_type=EventType.TASK_CREATED,
            action=request.task_type.value,
            resource_type="task",
            resource_id=request.task_id,
            session_id=request.session_id,
            metadata={
                "requested_by": request.requested_by,
                "priority": request.priority,
                "fiscal_year": request.fiscal_year,
                "fiscal_period": request.fiscal_period,
            },
        )
        write(created)

        # Resolve the sub-agent
        sub_agent = self._registry.get(request.task_type)
        if sub_agent is None:
            unregistered = AuditRecord.fail(
                agent_id=self.agent_id,
                event_type=EventType.TASK_FAILED,
                action=request.task_type.value,
                error=f"No agent registered for task type '{request.task_type.value}'",
                error_code="UNREGISTERED_TASK_TYPE",
                resource_type="task",
                resource_id=request.task_id,
                session_id=request.session_id,
            )
            write(unregistered)
            return TaskResult(
                task_id=request.task_id,
                task_type=request.task_type,
                agent_id=self.agent_id,
                status=EventStatus.FAILURE,
                error=f"No agent registered for '{request.task_type.value}'",
                audit_event_id=unregistered.event_id,
            )

        # Log delegation
        delegated = AuditRecord.ok(
            agent_id=self.agent_id,
            event_type=EventType.TASK_DELEGATED,
            action=request.task_type.value,
            resource_type="task",
            resource_id=request.task_id,
            session_id=request.session_id,
            metadata={"delegated_to": sub_agent.agent_id},
        )
        write(delegated)

        # Delegate via A2A transport (in-process now; HTTP-ready later)
        a2a_task = A2ATransport.make_task(sub_agent.agent_id, request)
        a2a_result = A2ATransport.send_task(sub_agent.agent_id, a2a_task)
        result = _a2a_to_task_result(a2a_result, request, sub_agent.agent_id)

        # Log final outcome at orchestrator level
        final_event = EventType.TASK_COMPLETE if result.ok else EventType.TASK_FAILED
        if result.ok:
            final_record = AuditRecord.ok(
                agent_id=self.agent_id,
                event_type=final_event,
                action=request.task_type.value,
                resource_type="task",
                resource_id=request.task_id,
                duration_ms=result.duration_ms,
                session_id=request.session_id,
                metadata={"sub_agent": sub_agent.agent_id, **result.output},
            )
        else:
            final_record = AuditRecord.fail(
                agent_id=self.agent_id,
                event_type=final_event,
                action=request.task_type.value,
                error=result.error or "Sub-agent returned FAILURE",
                resource_type="task",
                resource_id=request.task_id,
                session_id=request.session_id,
                metadata={"sub_agent": sub_agent.agent_id},
            )
            final_record.duration_ms = result.duration_ms
        write(final_record)

        return result


# ---------------------------------------------------------------------------
# Pre-register known sub-agents
# ---------------------------------------------------------------------------

from agents.bookkeeping        import BookkeepingAgent       # noqa: E402
from agents.gmail_comms        import GmailCommsAgent        # noqa: E402
from agents.journal_entry      import JournalEntryAgent      # noqa: E402
from agents.prepare_hst_return import PrepareHSTReturnAgent  # noqa: E402
from agents.rag                import RagAgent               # noqa: E402
from agents.sage50_ingest      import Sage50IngestAgent      # noqa: E402
from agents.sage50_odbc        import Sage50OdbcAgent        # noqa: E402
from agents.reconcile_gl       import ReconcileGLAgent       # noqa: E402

OrchestratorAgent.register(TaskType.BOOKKEEPING_RUN,      BookkeepingAgent())
OrchestratorAgent.register(TaskType.INGEST_SAGE50_CSV,     Sage50IngestAgent())
OrchestratorAgent.register(TaskType.INGEST_SAGE50_ODBC,    Sage50OdbcAgent())
OrchestratorAgent.register(TaskType.RECONCILE_GL,          ReconcileGLAgent())
OrchestratorAgent.register(TaskType.SEND_CLIENT_EMAIL,     GmailCommsAgent())
OrchestratorAgent.register(TaskType.PREPARE_HST_RETURN,    PrepareHSTReturnAgent())
OrchestratorAgent.register(TaskType.POST_JOURNAL_ENTRIES,  JournalEntryAgent())

_rag_agent = RagAgent()
OrchestratorAgent.register(TaskType.INDEX_DOCUMENT, _rag_agent)
OrchestratorAgent.register(TaskType.RAG_QUERY,      _rag_agent)

# Stubs for Phase 2+ — register when implemented:
# from agents.hst_return     import HSTReturnAgent
# from agents.t2_return      import T2ReturnAgent
# from agents.comms          import CommsAgent
# from agents.document       import DocumentAgent
# from agents.year_end       import YearEndAgent
