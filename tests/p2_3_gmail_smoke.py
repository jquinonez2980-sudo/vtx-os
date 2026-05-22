"""
tests/p2_3_gmail_smoke.py
P2.3 smoke test -- GmailCommsAgent (outbound email via Gmail API).

OFFLINE: no live GCP, no real Gmail API calls.
         _build_service() is mocked; no network access required.

Checks:
   1    GmailCommsAgent registered in OrchestratorAgent
   2    GmailCommsAgent registered in A2ATransport
   3    AgentCard is valid (name, url="/", non-empty skills)
   4    Direct handle(): TaskResult.ok is True (mocked service)
   5    Output contains message_id
   6    Output contains thread_id
   7    Output contains correct 'to' field
   8    Output contains correct 'subject' field
   9    Gmail service called with userId="me"
  10    MIME message has correct From header
  11    MIME message has correct To header
  12    MIME message has correct Subject header
  13    Multiple recipients joined in To header
  14    Missing credentials -> TaskResult.ok is False (no crash)
  15    Missing credentials error message is informative
  16    Orchestrator dispatch via A2A: TaskResult.ok is True
  17    Orchestrator dispatch result has message_id in output
  18    BQ audit events written (>= 2 for a direct run)
"""

from __future__ import annotations

import base64
import email
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# MockBQClient
# ---------------------------------------------------------------------------

class MockBQClient:
    def __init__(self):
        self.inserted: dict[str, list[dict]] = {}

    def get_table(self, table_id):
        from google.cloud.exceptions import NotFound
        raise NotFound(f"(mock) {table_id}")

    def create_table(self, table):
        return table

    def insert_rows_json(self, table_id, rows, **_):
        self.inserted.setdefault(str(table_id), []).extend(rows)
        return []

    def query(self, sql, **_):
        job = MagicMock()
        job.result.return_value = []
        return job


def _inject(client):
    import core.bq_loader, core.audit
    core.bq_loader._client = client
    core.audit._client     = client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_CREDS = json.dumps({
    "client_id":     "fake-client-id",
    "client_secret": "fake-client-secret",
    "refresh_token": "fake-refresh-token",
    "token_uri":     "https://oauth2.googleapis.com/token",
})

SEND_RESULT = {"id": "msg-abc123", "threadId": "thread-xyz789", "labelIds": ["SENT"]}


def _mock_gmail_service() -> MagicMock:
    svc = MagicMock()
    (
        svc.users.return_value
           .messages.return_value
           .send.return_value
           .execute.return_value
    ) = SEND_RESULT
    return svc


def _decode_raw(raw_b64: str) -> email.message.Message:
    raw_bytes = base64.urlsafe_b64decode(raw_b64 + "==")
    return email.message_from_bytes(raw_bytes)


def _email_payload(request_payload: dict) -> dict:
    return {
        "to":      "client@example.com",
        "subject": "December 2025 Bookkeeping Summary",
        "body":    "Your books for December 2025 are ready for review.",
        **request_payload,
    }


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------

def run() -> None:
    mock_bq = MockBQClient()
    _inject(mock_bq)

    # Import after injection
    from agents.a2a import A2ATransport, AgentCard
    from agents.base import TaskRequest, TaskType
    from agents.gmail_comms import GmailCommsAgent, _build_raw_message
    from agents.orchestrator import OrchestratorAgent   # triggers all registrations
    import core.secrets

    checks: list[tuple[str, bool]] = []

    # ------------------------------------------------------------------
    # 1-2  Registration checks
    # ------------------------------------------------------------------
    checks.append(("GmailCommsAgent in OrchestratorAgent registry",
                   TaskType.SEND_CLIENT_EMAIL in OrchestratorAgent.registered_types()))
    checks.append(("GmailCommsAgent in A2ATransport",
                   "gmail-comms-agent" in A2ATransport.registered_ids()))

    # ------------------------------------------------------------------
    # 3  Agent Card
    # ------------------------------------------------------------------
    try:
        card = A2ATransport.agent_card("gmail-comms-agent")
        card_ok = (
            isinstance(card, AgentCard)
            and card.name == "gmail-comms-agent"
            and card.url == "/"
            and len(card.skills) > 0
            and card.version
        )
    except Exception:
        card_ok = False
    checks.append(("AgentCard valid for gmail-comms-agent", card_ok))

    # ------------------------------------------------------------------
    # 4-12  Direct agent run (mocked Gmail service)
    # ------------------------------------------------------------------
    mock_svc = _mock_gmail_service()
    agent = GmailCommsAgent()
    req = TaskRequest(
        task_type=TaskType.SEND_CLIENT_EMAIL,
        requested_by="test@vtx-os.local",
        payload=_email_payload({}),
    )

    with patch("agents.gmail_comms._build_service", return_value=mock_svc), \
         patch("agents.gmail_comms._load_creds_json", return_value=FAKE_CREDS):
        result = agent.run(req)

    checks.append(("TaskResult.ok is True",                result.ok))
    checks.append(("Output contains message_id",           "message_id" in result.output))
    checks.append(("Output contains thread_id",            "thread_id"  in result.output))
    checks.append(("Output 'to' matches recipient",
                   result.output.get("to") == "client@example.com"))
    checks.append(("Output 'subject' matches",
                   result.output.get("subject") == "December 2025 Bookkeeping Summary"))

    # Verify the Gmail API was called correctly
    send_mock = mock_svc.users.return_value.messages.return_value.send
    if send_mock.call_count == 1:
        call_kwargs = send_mock.call_args.kwargs
        userid_ok = call_kwargs.get("userId") == "me"
        raw_b64   = call_kwargs.get("body", {}).get("raw", "")
        parsed    = _decode_raw(raw_b64)

        checks.append(("Gmail API called with userId='me'",      userid_ok))
        checks.append(("MIME From == firm address",
                       parsed["From"] == "jquinonez2980@gmail.com"))
        checks.append(("MIME To == recipient",
                       parsed["To"] == "client@example.com"))
        checks.append(("MIME Subject matches",
                       parsed["Subject"] == "December 2025 Bookkeeping Summary"))
    else:
        checks.extend([
            ("Gmail API called with userId='me'", False),
            ("MIME From == firm address",         False),
            ("MIME To == recipient",              False),
            ("MIME Subject matches",              False),
        ])

    # ------------------------------------------------------------------
    # 13  Multiple recipients joined in To header
    # ------------------------------------------------------------------
    raw_multi = _build_raw_message(
        from_addr="jquinonez2980@gmail.com",
        to="a@example.com, b@example.com",
        subject="Test",
        body="Body",
    )
    parsed_multi = _decode_raw(raw_multi)
    checks.append(("Multiple recipients joined in To header",
                   "a@example.com" in (parsed_multi["To"] or "") and
                   "b@example.com" in (parsed_multi["To"] or "")))

    # ------------------------------------------------------------------
    # 14-15  Missing credentials -> graceful FAILURE
    # ------------------------------------------------------------------
    agent2 = GmailCommsAgent()
    req2 = TaskRequest(
        task_type=TaskType.SEND_CLIENT_EMAIL,
        requested_by="test@vtx-os.local",
        payload=_email_payload({}),
    )
    with patch("agents.gmail_comms._load_creds_json",
               side_effect=ValueError("Secret 'vtx-gmail-oauth-credentials' has not been set")):
        bad_result = agent2.run(req2)

    checks.append(("Missing credentials -> TaskResult.ok is False", not bad_result.ok))
    checks.append(("Missing credentials error message is set",
                   bad_result.error is not None and "vtx-gmail-oauth-credentials" in (bad_result.error or "")))

    # ------------------------------------------------------------------
    # 16-17  Orchestrator dispatch via A2A
    # ------------------------------------------------------------------
    mock_svc2 = _mock_gmail_service()
    req3 = TaskRequest(
        task_type=TaskType.SEND_CLIENT_EMAIL,
        requested_by="test@vtx-os.local",
        payload=_email_payload({}),
    )

    with patch("agents.gmail_comms._build_service", return_value=mock_svc2), \
         patch("agents.gmail_comms._load_creds_json", return_value=FAKE_CREDS):
        orch_result = OrchestratorAgent().run(req3)

    checks.append(("Orchestrator A2A dispatch: TaskResult.ok is True", orch_result.ok))
    checks.append(("Orchestrator result has message_id",
                   "message_id" in orch_result.output))

    # ------------------------------------------------------------------
    # 18  Audit events written
    # ------------------------------------------------------------------
    audit_rows = sum(len(v) for k, v in mock_bq.inserted.items() if "audit_log" in k)
    checks.append(("BQ audit events written (>= 2)", audit_rows >= 2))

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------
    print("=" * 60)
    print("P2.3 Gmail Comms Smoke Test")
    print("=" * 60)
    passed = 0
    for desc, ok in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {desc}")
        if ok:
            passed += 1

    total = len(checks)
    print(f"\n{passed}/{total} checks passed")
    print(f"BQ audit rows written: {audit_rows}")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    run()
