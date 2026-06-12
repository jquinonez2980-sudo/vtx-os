"""
tests/p2_2_a2a_smoke.py
P2.2 smoke test — A2A protocol wiring.

OFFLINE: no live GCP or Vertex AI calls.
         Uses MockBQClient and local test-data files.

Checks:
   1-4   A2ATransport has all 4 agents registered
   5-8   Each registered agent has a valid AgentCard (name, url, skills non-empty)
   9     Direct A2ATransport.make_task() produces SUBMITTED state
  10     After send_task() the state is COMPLETED
  11     Artifacts contain a task_result key
  12-14  task_result reconciliation figures match expected values
  15     A2ATask.session_id matches the original TaskRequest.session_id
  16     Orchestrator dispatch via A2A returns TaskResult.ok == True
  17     TaskResult matched_count == 19 (same as P2.1 live test)
  18     BQ audit events written (>= 4)
  19     Reconciliation rows written to BQ (44 = 22 direct + 22 via orchestrator)
  20     send_task() for an unknown agent_id returns FAILED state
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _find(name: str) -> str:
    real = _ROOT / "data" / "test-client" / name
    return str(real if real.exists() else _ROOT / "tests" / "fixtures" / name)


# ---------------------------------------------------------------------------
# MockBQClient (same pattern as P1.7 and P2.1)
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
# Test helpers
# ---------------------------------------------------------------------------

EXPECTED_AGENT_IDS = {
    "bookkeeping-agent",
    "sage50-ingest-agent",
    "sage50-odbc-agent",
    "reconcile-gl-agent",
}

GL_CSV   = _find("concetta-dec2025-gl.csv")
BANK_CSV = _find("dec-2025-bank-extracted.csv")


def _reconcile_payload() -> dict:
    return {
        "gl_csv_path":   GL_CSV,
        "account_no":    "xxxx5443",
        "period":        "2025-12",
        "gl_bank_account": "1060",
        "bank_csv_path": BANK_CSV,
    }


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------

def run() -> None:
    mock = MockBQClient()
    _inject(mock)

    # Import after injection so BQ singletons are replaced
    from agents.a2a import (
        A2ADataPart, A2AMessage, A2ARole, A2ATaskState, A2ATextPart,
        A2ATransport, AgentCard,
    )
    from agents.base import TaskRequest, TaskType
    from agents.orchestrator import OrchestratorAgent   # triggers registration

    checks: list[tuple[str, bool]] = []

    # ------------------------------------------------------------------
    # 1-4  A2ATransport registration
    # ------------------------------------------------------------------
    registered = set(A2ATransport.registered_ids())
    for aid in EXPECTED_AGENT_IDS:
        checks.append((f"'{aid}' registered in A2ATransport", aid in registered))

    # ------------------------------------------------------------------
    # 5-8  Agent Cards
    # ------------------------------------------------------------------
    for aid in EXPECTED_AGENT_IDS:
        try:
            card = A2ATransport.agent_card(aid)
            valid = (
                isinstance(card, AgentCard)
                and card.name == aid
                and card.url == "/"
                and len(card.skills) > 0
                and card.version
            )
        except Exception:
            valid = False
        checks.append((f"AgentCard valid for '{aid}'", valid))

    # ------------------------------------------------------------------
    # 9  Direct make_task produces SUBMITTED state
    # ------------------------------------------------------------------
    req = TaskRequest(
        task_type=TaskType.RECONCILE_GL,
        requested_by="test@vtx-os.local",
        payload=_reconcile_payload(),
    )
    a2a_task = A2ATransport.make_task("reconcile-gl-agent", req)
    checks.append(("make_task() produces SUBMITTED state",
                   a2a_task.status.state == A2ATaskState.SUBMITTED))

    # ------------------------------------------------------------------
    # 10-14  Direct send_task (bypasses orchestrator)
    # ------------------------------------------------------------------
    a2a_result = A2ATransport.send_task("reconcile-gl-agent", a2a_task)
    checks.append(("send_task() returns COMPLETED state",
                   a2a_result.status.state == A2ATaskState.COMPLETED))

    has_artifact = bool(a2a_result.artifacts and "task_result" in a2a_result.artifacts[0])
    checks.append(("Artifact contains task_result key", has_artifact))

    tr = a2a_result.artifacts[0].get("task_result", {}) if has_artifact else {}
    checks.append(("task_result matched_count == 19", tr.get("output", {}).get("matched_count") == 19))
    checks.append(("task_result unmatched_bank_count == 1",
                   tr.get("output", {}).get("unmatched_bank_count") == 1))
    checks.append(("task_result unmatched_gl_count == 2",
                   tr.get("output", {}).get("unmatched_gl_count") == 2))

    # ------------------------------------------------------------------
    # 15  session_id propagates from TaskRequest into A2ATask
    # ------------------------------------------------------------------
    checks.append(("A2ATask.session_id matches TaskRequest.session_id",
                   a2a_task.session_id == req.session_id))

    # ------------------------------------------------------------------
    # 16-17  Full orchestrator dispatch (also goes through A2A internally)
    # ------------------------------------------------------------------
    req2 = TaskRequest(
        task_type=TaskType.RECONCILE_GL,
        requested_by="test@vtx-os.local",
        payload=_reconcile_payload(),
    )
    orch = OrchestratorAgent()
    result = orch.run(req2)

    checks.append(("Orchestrator dispatch via A2A: TaskResult.ok is True", result.ok))
    checks.append(("Orchestrator result matched_count == 19",
                   result.output.get("matched_count") == 19))

    # ------------------------------------------------------------------
    # 18-19  BQ writes confirm audit + reconciliation rows
    # ------------------------------------------------------------------
    audit_rows = sum(len(v) for k, v in mock.inserted.items() if "audit_log" in k)
    recon_rows = sum(len(v) for k, v in mock.inserted.items() if "gl_reconciliation" in k)

    checks.append(("BQ audit events written (>= 4)", audit_rows >= 4))
    checks.append(("44 reconciliation rows written to BQ (22 x2 runs)", recon_rows == 44))

    # ------------------------------------------------------------------
    # 20  Unknown agent returns FAILED
    # ------------------------------------------------------------------
    dummy_task = A2ATransport.make_task("reconcile-gl-agent", req)
    failed = A2ATransport.send_task("does-not-exist-agent", dummy_task)
    checks.append(("Unknown agent returns FAILED state",
                   failed.status.state == A2ATaskState.FAILED))

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------
    print("=" * 60)
    print("P2.2 A2A Smoke Test — Protocol Wiring")
    print("=" * 60)
    passed = 0
    for desc, ok in checks:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {desc}")
        if ok:
            passed += 1

    total = len(checks)
    print(f"\n{passed}/{total} checks passed")
    print()
    print(f"A2A registered agents : {sorted(A2ATransport.registered_ids())}")
    print(f"BQ audit rows written : {audit_rows}")
    print(f"BQ recon rows written : {recon_rows}")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    run()
