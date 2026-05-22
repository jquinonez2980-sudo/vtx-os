"""
agents/a2a.py
A2A (Agent-to-Agent) protocol types and in-process transport layer.

Implements a subset of the Google A2A specification sufficient for VTX-OS
inter-agent communication.  The in-process A2ATransport can be swapped for
HTTP without changing orchestrator or sub-agent code.

Exports:
    A2ATextPart, A2ADataPart, A2APart  — message content types
    A2AMessage                          — role (user | agent) + parts
    A2ATaskState                        — submitted / working / completed / failed
    A2ATaskStatus                       — state + optional explanatory message
    A2ATask                             — the unit of work passed between agents
    AgentSkill, AgentCard               — agent capability descriptor (/.well-known/agent.json)
    A2AAgentServer                      — wraps AgentBase as an A2A-compatible endpoint
    A2ATransport                        — routes A2ATasks to registered servers
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Message content parts
# ---------------------------------------------------------------------------

class A2ATextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class A2ADataPart(BaseModel):
    type: Literal["data"] = "data"
    data: dict[str, Any]
    mime_type: str = "application/json"


A2APart = Annotated[Union[A2ATextPart, A2ADataPart], Field(discriminator="type")]


class A2ARole(str, Enum):
    USER  = "user"
    AGENT = "agent"


class A2AMessage(BaseModel):
    role: A2ARole
    parts: list[A2APart]


# ---------------------------------------------------------------------------
# Task lifecycle
# ---------------------------------------------------------------------------

class A2ATaskState(str, Enum):
    SUBMITTED      = "submitted"
    WORKING        = "working"
    COMPLETED      = "completed"
    FAILED         = "failed"
    CANCELED       = "canceled"
    INPUT_REQUIRED = "input-required"


class A2ATaskStatus(BaseModel):
    state: A2ATaskState
    message: A2AMessage | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class A2ATask(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    message: A2AMessage           # input from the calling agent / user
    status: A2ATaskStatus = Field(
        default_factory=lambda: A2ATaskStatus(state=A2ATaskState.SUBMITTED)
    )
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Agent Card  (/.well-known/agent.json equivalent)
# ---------------------------------------------------------------------------

class AgentSkill(BaseModel):
    id: str
    name: str
    description: str
    input_modes: list[str] = ["application/json"]
    output_modes: list[str] = ["application/json"]


class AgentCard(BaseModel):
    name: str
    description: str
    url: str = "/"          # "/" = in-process; set to HTTP URL for remote agents
    version: str = "0.1.0"
    skills: list[AgentSkill] = Field(default_factory=list)
    capabilities: dict[str, bool] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# A2AAgentServer — wraps a VTX AgentBase as an A2A-compatible endpoint
# ---------------------------------------------------------------------------

class A2AAgentServer:
    """Accepts A2ATask objects, runs the underlying VTX agent, returns the
    updated task.  State transitions: SUBMITTED → WORKING → COMPLETED | FAILED.
    """

    def __init__(self, agent: object) -> None:
        self._agent = agent

    @property
    def agent_card(self) -> AgentCard:
        return AgentCard(
            name=self._agent.agent_id,       # type: ignore[attr-defined]
            description=f"VTX agent: {self._agent.agent_id}",  # type: ignore[attr-defined]
            url="/",
            version=self._agent.agent_version,  # type: ignore[attr-defined]
            skills=[AgentSkill(
                id=self._agent.agent_id,         # type: ignore[attr-defined]
                name=self._agent.agent_id,       # type: ignore[attr-defined]
                description=f"Handles tasks dispatched to {self._agent.agent_id}",  # type: ignore[attr-defined]
            )],
        )

    def process_task(self, a2a_task: A2ATask) -> A2ATask:
        """Run the agent and return the task with COMPLETED or FAILED status."""
        working = a2a_task.model_copy(
            update={"status": A2ATaskStatus(state=A2ATaskState.WORKING)}
        )
        try:
            request = self._extract_request(working)
            result = self._agent.run(request)   # type: ignore[attr-defined]
        except Exception as exc:
            return working.model_copy(update={
                "status": A2ATaskStatus(
                    state=A2ATaskState.FAILED,
                    message=A2AMessage(
                        role=A2ARole.AGENT,
                        parts=[A2ATextPart(text=f"Agent raised exception: {exc}")],
                    ),
                ),
            })

        if result.ok:
            return working.model_copy(update={
                "status": A2ATaskStatus(
                    state=A2ATaskState.COMPLETED,
                    message=A2AMessage(
                        role=A2ARole.AGENT,
                        parts=[A2ADataPart(data=result.output)],
                    ),
                ),
                "artifacts": [{"task_result": result.model_dump(mode="json")}],
                "metadata": {
                    **working.metadata,
                    "agent_id": result.agent_id,
                    "duration_ms": result.duration_ms,
                },
            })
        return working.model_copy(update={
            "status": A2ATaskStatus(
                state=A2ATaskState.FAILED,
                message=A2AMessage(
                    role=A2ARole.AGENT,
                    parts=[A2ATextPart(text=result.error or "Agent returned FAILURE")],
                ),
            ),
            "artifacts": [{"task_result": result.model_dump(mode="json")}],
        })

    def _extract_request(self, a2a_task: A2ATask) -> object:
        """Pull a TaskRequest from the first data part of the A2ATask message."""
        from agents.base import TaskRequest
        for part in a2a_task.message.parts:
            if isinstance(part, A2ADataPart):
                return TaskRequest(**part.data)
        raise ValueError(
            f"A2A task {a2a_task.id} has no data part — cannot reconstruct TaskRequest"
        )


# ---------------------------------------------------------------------------
# A2ATransport — in-process registry and routing
# ---------------------------------------------------------------------------

class A2ATransport:
    """Routes A2ATasks to registered agent servers.

    All servers are currently in-process.  To move an agent to a remote
    process, replace its entry in _registry with an HTTP-based server
    implementation that has the same process_task() signature.
    """

    _registry: dict[str, A2AAgentServer] = {}

    @classmethod
    def register(cls, agent_id: str, server: A2AAgentServer) -> None:
        cls._registry[agent_id] = server

    @classmethod
    def registered_ids(cls) -> list[str]:
        return list(cls._registry.keys())

    @classmethod
    def agent_card(cls, agent_id: str) -> AgentCard:
        server = cls._registry.get(agent_id)
        if server is None:
            raise KeyError(f"No A2A server registered for agent_id '{agent_id}'")
        return server.agent_card

    @classmethod
    def all_agent_cards(cls) -> list[AgentCard]:
        return [s.agent_card for s in cls._registry.values()]

    @classmethod
    def send_task(cls, agent_id: str, a2a_task: A2ATask) -> A2ATask:
        """Route the task to the named server; return FAILED if unregistered."""
        server = cls._registry.get(agent_id)
        if server is None:
            return a2a_task.model_copy(update={
                "status": A2ATaskStatus(
                    state=A2ATaskState.FAILED,
                    message=A2AMessage(
                        role=A2ARole.AGENT,
                        parts=[A2ATextPart(
                            text=f"No A2A server registered for agent '{agent_id}'"
                        )],
                    ),
                ),
            })
        return server.process_task(a2a_task)

    @classmethod
    def make_task(cls, agent_id: str, request: object) -> A2ATask:
        """Build an A2ATask that wraps a TaskRequest (convenience helper)."""
        from agents.base import TaskRequest
        req: TaskRequest = request  # type: ignore[assignment]
        return A2ATask(
            session_id=req.session_id,
            message=A2AMessage(
                role=A2ARole.USER,
                parts=[A2ADataPart(data=req.model_dump(mode="json"))],
            ),
            metadata={"target_agent": agent_id, "task_type": req.task_type.value},
        )
