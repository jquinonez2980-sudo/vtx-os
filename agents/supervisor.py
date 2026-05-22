"""
agents/supervisor.py
SupervisorAgent — top-level ADK LlmAgent that converts natural language
accounting requests into VTX TaskRequest dispatches.

The supervisor has one tool: dispatch_task(task_type, payload_json).
It calls the existing OrchestratorAgent, which in turn calls the registered
sub-agent (BookkeepingAgent, ReconcileGLAgent, PrepareHSTReturnAgent, etc.).

Requires (in environment or config/project.env):
    GOOGLE_GENAI_USE_VERTEXAI   = TRUE
    GOOGLE_CLOUD_PROJECT        = vtx-accounting-os-prod
    GOOGLE_CLOUD_LOCATION       = northamerica-northeast1
"""

from __future__ import annotations

import json

from google.adk.agents import LlmAgent

MODEL = "gemini-2.5-flash"

_INSTRUCTION = """
You are the VTX Accounting OS supervisor agent for a Canadian accounting firm.
Your job is to understand accounting task requests and dispatch them to the
correct sub-agent using the dispatch_task tool.

Call dispatch_task exactly once per user request with:
  - task_type: one of the supported types below
  - payload_json: a JSON string with the required parameters for that task

Supported task types and their required payload keys:

  BOOKKEEPING_RUN
    csv_path (str): path to bank statement CSV or PDF
    account_no (str): masked account identifier, e.g. "xxxx5443"
    bank_code (str, optional): RBC, TD, BMO, CIBC, SCOTIABANK, NATIONAL, DESJARDINS

  INGEST_SAGE50_CSV
    local_path (str): path to Sage 50 CSV export
    report_type (str): gl_transactions | ar_invoices | ap_bills | chart_of_accounts
                       | customers | vendors | tax_summary | payroll
                       | inventory | bank_reconciliation

  INGEST_SAGE50_ODBC
    report_type (str): same list as INGEST_SAGE50_CSV

  RECONCILE_GL
    gl_csv_path (str): path to Sage 50 GL export CSV
    account_no (str): masked bank account identifier
    period (str): YYYY-MM, e.g. "2025-12"
    gl_bank_account (str, optional): GL account number, default "1060"
    amount_tolerance (float, optional): max dollar diff for a match, default 1.00
    date_tolerance_days (int, optional): max days apart for a match, default 2
    bank_csv_path (str, optional): local bank CSV instead of querying BigQuery

  PREPARE_HST_RETURN
    tax_csv_path (str): path to Sage 50 Sales Tax Summary CSV
    return_period (str): YYYY-MM
    business_no (str, optional): CRA Business Number, e.g. "123456789RT0001"

  SEND_CLIENT_EMAIL
    to (str): recipient email address (e.g. "client@example.com")
    subject (str): email subject line
    body (str): plain-text email body
    cc (str, optional): CC recipient email address
    html_body (str, optional): HTML version of the body

  INDEX_DOCUMENT
    document_type (str): engagement_letter | t2_return | hst_return | gl_summary
                         | bank_recon | chart_of_accounts | generic
    client_id (str): client identifier slug, e.g. "concetta-enterprises"
    source_text (str): full document text to chunk, embed, and store
    fiscal_year (int, optional): e.g. 2025
    fiscal_period (str, optional): e.g. "2025-Q4"
    source_uri (str, optional): gs:// path to the original file

  RAG_QUERY
    query (str): natural-language question to search over indexed documents
    client_id (str, optional): filter results to a specific client
    document_type (str, optional): filter to a specific document type
    fiscal_year (int, optional): filter to a specific fiscal year
    top_k (int, optional): number of context chunks to return, default 5

After dispatch_task returns, summarise the key figures for the user:
  - GL reconciliation: matched count, unmatched items, net difference, is_reconciled
  - Bookkeeping: transaction count, auto-categorized vs needs_review, net movement
  - HST return: Line 101, 103, 106, 109, filing_due_date, is_refund
  - Email sent: recipient, subject, Gmail message_id
  - Do NOT reproduce the full raw JSON — give a concise accounting summary
"""


def dispatch_task(task_type: str, payload_json: str) -> str:
    """Dispatch an accounting task to the VTX orchestrator.

    Args:
        task_type: VTX task type (e.g. "RECONCILE_GL", "BOOKKEEPING_RUN").
        payload_json: JSON string with the task-specific payload parameters.

    Returns:
        JSON string with status, output fields, or error message.
    """
    from agents.base import TaskRequest, TaskType
    from agents.orchestrator import OrchestratorAgent

    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        return json.dumps({"status": "FAILURE", "error": f"Invalid payload JSON: {exc}"})

    try:
        task_type_enum = TaskType(task_type)
    except ValueError:
        valid = [t.value for t in TaskType]
        return json.dumps({
            "status": "FAILURE",
            "error": f"Unknown task_type '{task_type}'. Valid values: {valid}",
        })

    req = TaskRequest(task_type=task_type_enum, payload=payload)
    result = OrchestratorAgent().run(req)

    if result.ok:
        return json.dumps({"status": "SUCCESS", "output": result.output})
    return json.dumps({"status": "FAILURE", "error": result.error or "Unknown error"})


supervisor_agent = LlmAgent(
    name="vtx_supervisor",
    model=MODEL,
    description="VTX Accounting OS supervisor — dispatches accounting tasks to registered sub-agents",
    instruction=_INSTRUCTION,
    tools=[dispatch_task],
)
