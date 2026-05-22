"""
agents/adk_runner.py
ADK Runner for VTX-OS.

Exports:
    run_sync(user_message, session_id=None) → str
        Synchronous entry point. Sends one message to the SupervisorAgent
        and returns its final text response.

    runner   — the ADK Runner instance (for async callers)

Requires in environment or config/project.env:
    GOOGLE_GENAI_USE_VERTEXAI   = TRUE
    GOOGLE_CLOUD_PROJECT        = vtx-accounting-os-prod
    GOOGLE_CLOUD_LOCATION       = northamerica-northeast1
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

# Load config/project.env before any Google SDK imports so env vars are in place
_env_file = Path(__file__).resolve().parents[1] / "config" / "project.env"
if _env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_file, override=False)

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.supervisor import supervisor_agent

APP_NAME = "vtx-os"
_USER_ID  = "vtx-system"

_session_service = InMemorySessionService()

runner = Runner(
    agent=supervisor_agent,
    app_name=APP_NAME,
    session_service=_session_service,
)


async def _run_async(user_message: str, session_id: str) -> str:
    """One async turn through the supervisor agent."""
    await _session_service.create_session(
        app_name=APP_NAME,
        user_id=_USER_ID,
        session_id=session_id,
    )
    content = types.Content(role="user", parts=[types.Part(text=user_message)])
    final_text = ""
    async for event in runner.run_async(
        user_id=_USER_ID,
        session_id=session_id,
        new_message=content,
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = event.content.parts[0].text or ""
    return final_text


def run_sync(user_message: str, session_id: str | None = None) -> str:
    """Run one supervisor turn synchronously and return the response text."""
    sid = session_id or str(uuid.uuid4())
    return asyncio.run(_run_async(user_message, sid))
