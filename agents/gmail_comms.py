"""
agents/gmail_comms.py
GmailCommsAgent — send outbound emails via the Gmail API.

Handles TaskType.SEND_CLIENT_EMAIL.

Required payload keys:
    to      (str | list[str])  — recipient(s)
    subject (str)              — email subject
    body    (str)              — plain-text body

Optional payload keys:
    cc        (str | list[str])  — CC recipients
    html_body (str)              — HTML alternative body (plain-text body still required)

Returns TaskResult.output:
    message_id  — Gmail message ID (for threading / audit linkback)
    thread_id   — Gmail thread ID
    to          — resolved To header string
    subject     — subject as sent

Credentials:
    Stored in Secret Manager as 'vtx-gmail-oauth-credentials' (JSON).
    Format (google.oauth2.credentials authorized_user):
        {
          "client_id":     "...",
          "client_secret": "...",
          "refresh_token": "...",
          "token_uri":     "https://oauth2.googleapis.com/token"
        }

    Local dev override:
        VTX_SECRET_VTX_GMAIL_OAUTH_CREDENTIALS='{"client_id":...}'

    To obtain credentials the first time, run:
        python scripts/gmail_auth.py
    (see scripts/ directory for the interactive OAuth flow helper)

Graceful degradation:
    If credentials are not configured, the agent returns TaskResult with
    status=FAILURE and a clear error message — it does NOT raise or crash.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any

from agents.base import AgentBase, TaskRequest, TaskResult, TaskType
from models.base import EventStatus

FROM_EMAIL = "jquinonez2980@gmail.com"
CREDENTIAL_SECRET = "vtx-gmail-oauth-credentials"


class GmailCommsAgent(AgentBase):
    agent_id = "gmail-comms-agent"

    def handle(self, request: TaskRequest) -> TaskResult:
        payload = request.payload

        to       = payload["to"]
        subject  = payload["subject"]
        body     = payload["body"]
        cc       = payload.get("cc")
        html_body = payload.get("html_body")

        # --- Load credentials ---
        try:
            creds_json = _load_creds_json()
        except ValueError as exc:
            return TaskResult(
                task_id=request.task_id,
                task_type=TaskType.SEND_CLIENT_EMAIL,
                agent_id=self.agent_id,
                status=EventStatus.FAILURE,
                error=str(exc),
            )

        # --- Build Gmail service ---
        try:
            service = _build_service(creds_json)
        except Exception as exc:
            return TaskResult(
                task_id=request.task_id,
                task_type=TaskType.SEND_CLIENT_EMAIL,
                agent_id=self.agent_id,
                status=EventStatus.FAILURE,
                error=f"Failed to build Gmail service: {exc}",
            )

        # --- Build and send message ---
        to_header = to if isinstance(to, str) else ", ".join(to)
        raw = _build_raw_message(
            from_addr=FROM_EMAIL,
            to=to_header,
            subject=subject,
            body=body,
            cc=(cc if isinstance(cc, str) else ", ".join(cc)) if cc else None,
            html_body=html_body,
        )

        try:
            sent: dict[str, Any] = (
                service.users()
                .messages()
                .send(userId="me", body={"raw": raw})
                .execute()
            )
        except Exception as exc:
            return TaskResult(
                task_id=request.task_id,
                task_type=TaskType.SEND_CLIENT_EMAIL,
                agent_id=self.agent_id,
                status=EventStatus.FAILURE,
                error=f"Gmail API send failed: {exc}",
            )

        return TaskResult(
            task_id=request.task_id,
            task_type=TaskType.SEND_CLIENT_EMAIL,
            agent_id=self.agent_id,
            status=EventStatus.SUCCESS,
            output={
                "message_id": sent.get("id", ""),
                "thread_id":  sent.get("threadId", ""),
                "to":         to_header,
                "subject":    subject,
                "body":       body,
            },
        )


# ---------------------------------------------------------------------------
# Internal helpers (separated for testability)
# ---------------------------------------------------------------------------

def _load_creds_json() -> str:
    """Return the raw credential JSON string from Secret Manager (or env override)."""
    from core.secrets import get
    return get(CREDENTIAL_SECRET)


def _build_service(creds_json: str) -> Any:
    """Build and return an authenticated Gmail API service object."""
    import json
    import google.oauth2.credentials
    from googleapiclient.discovery import build

    info = json.loads(creds_json)
    creds = google.oauth2.credentials.Credentials.from_authorized_user_info(
        info, scopes=[
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.modify",
        ]
    )
    return build("gmail", "v1", credentials=creds)


def _build_raw_message(
    from_addr: str,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    html_body: str | None = None,
) -> str:
    """Build a base64url-encoded RFC 2822 message suitable for the Gmail API."""
    msg = EmailMessage()
    msg["From"]    = from_addr
    msg["To"]      = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc

    if html_body:
        msg.set_content(body)
        msg.add_alternative(html_body, subtype="html")
    else:
        msg.set_content(body)

    return base64.urlsafe_b64encode(msg.as_bytes()).decode()
