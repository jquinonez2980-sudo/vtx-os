"""
BigQuery-backed approval queue.

Table: vtx_accounting.approval_queue
  - Rows are streamed in via core/bq_loader (insert)
  - Rows are updated via BQ DML (approve / reject / escalate)

Usage:
    from core.approval_queue import submit, get_pending, approve, reject

    items = submit(needs_review_txns, session_id="abc", period="2025-12")
    pending = get_pending(limit=50)
    approve(pending[0].queue_id, reviewer_email="accountant@firm.ca",
            final_gl_no="4100", note="Revenue — Acme retainer")
"""

from __future__ import annotations

import os
import sys

from google.cloud import bigquery

from core.bq_loader import ensure_table, load_rows
from models.approval import ApprovalItem, ApprovalStatus

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "vtx-accounting-os-prod")
DATASET = "vtx_accounting"
QUEUE_TABLE = "approval_queue"
TABLE_ID = f"{PROJECT}.{DATASET}.{QUEUE_TABLE}"

_QUEUE_CFG = {
    "partition_field": "txn_date",
    "cluster_fields":  ["status", "bank_code"],
}

_bq_client: bigquery.Client | None = None


def _bq() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=PROJECT)
    return _bq_client


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def submit(
    categorized_txns,           # list[CategorizedTransaction]
    session_id: str = "",
    period: str = "",
) -> list[ApprovalItem]:
    """Convert needs_review transactions to ApprovalItems and stream to BQ."""
    items = [
        ApprovalItem.from_categorized(t, session_id=session_id, period=period)
        for t in categorized_txns
        if t.needs_review
    ]
    if not items:
        return []

    ensure_table(DATASET, QUEUE_TABLE, ApprovalItem, **_QUEUE_CFG)
    load_rows(DATASET, QUEUE_TABLE, items, session_id=session_id)
    return items


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_pending(
    limit: int = 100,
    account_no: str | None = None,
    account_nos: list[str] | None = None,
    period: str | None = None,
) -> list[ApprovalItem]:
    """Return PENDING items ordered by date ascending (oldest first).

    account_nos: when a company has multiple bank accounts (e.g. Theotherapy BMO + RBC),
    pass all masked account numbers to fetch them together.
    period: bookkeeping period string 'YYYY-MM' — filters by the period field
    (the month the statement was ingested), not txn_date (the actual transaction date).
    """
    where = "status = 'PENDING'"
    params: list[bigquery.ScalarQueryParameter] = []
    # account_nos takes priority; account_no is kept for backward compat
    accts = account_nos or ([account_no] if account_no else None)
    if accts:
        if len(accts) == 1:
            where += " AND account_no = @account_no"
            params.append(bigquery.ScalarQueryParameter("account_no", "STRING", accts[0]))
        else:
            where += " AND account_no IN UNNEST(@account_nos)"
            params.append(bigquery.ArrayQueryParameter("account_nos", "STRING", accts))
    if period:
        where += " AND period = @period"
        params.append(bigquery.ScalarQueryParameter("period", "STRING", period))
    query = f"""
        SELECT * EXCEPT (_loaded_at, _session_id)
        FROM `{TABLE_ID}`
        WHERE {where}
        ORDER BY txn_date ASC, created_at ASC
        LIMIT {limit}
    """
    job_cfg = bigquery.QueryJobConfig(query_parameters=params) if params else None
    rows = list(_bq().query(query, job_config=job_cfg).result())
    return [ApprovalItem.model_validate(dict(row.items())) for row in rows]


def get_by_period(period: str) -> list[ApprovalItem]:
    """Return all items for a given YYYY-MM period, any status."""
    query = f"""
        SELECT * EXCEPT (_loaded_at, _session_id)
        FROM `{TABLE_ID}`
        WHERE period = @period
        ORDER BY txn_date ASC
    """
    job_cfg = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("period", "STRING", period)]
    )
    rows = list(_bq().query(query, job_config=job_cfg).result())
    return [ApprovalItem.model_validate(dict(row.items())) for row in rows]


# ---------------------------------------------------------------------------
# Update (DML)
# ---------------------------------------------------------------------------

def _update_status(
    queue_id: str,
    new_status: ApprovalStatus,
    reviewer_email: str,
    final_gl_no: str | None,
    note: str,
) -> bool:
    query = f"""
        UPDATE `{TABLE_ID}`
        SET status         = @status,
            reviewer_email = @reviewer_email,
            reviewed_at    = CURRENT_TIMESTAMP(),
            review_note    = @note
            {', final_gl_no = @final_gl_no' if final_gl_no else ''}
        WHERE queue_id = @queue_id
          AND status   = 'PENDING'
    """
    params = [
        bigquery.ScalarQueryParameter("status",         "STRING", new_status.value),
        bigquery.ScalarQueryParameter("reviewer_email", "STRING", reviewer_email),
        bigquery.ScalarQueryParameter("note",           "STRING", note),
        bigquery.ScalarQueryParameter("queue_id",       "STRING", queue_id),
    ]
    if final_gl_no:
        params.append(bigquery.ScalarQueryParameter("final_gl_no", "STRING", final_gl_no))

    try:
        job = _bq().query(query, job_config=bigquery.QueryJobConfig(query_parameters=params))
        job.result()
        return True
    except Exception as exc:
        print(f"[approval_queue] UPDATE failed for {queue_id}: {exc}", file=sys.stderr)
        return False


def approve(
    queue_id: str,
    reviewer_email: str,
    final_gl_no: str,
    note: str = "",
) -> bool:
    return _update_status(queue_id, ApprovalStatus.APPROVED, reviewer_email, final_gl_no, note)


def reject(
    queue_id: str,
    reviewer_email: str,
    note: str = "",
) -> bool:
    return _update_status(queue_id, ApprovalStatus.REJECTED, reviewer_email, None, note)


def escalate(
    queue_id: str,
    reviewer_email: str,
    note: str = "",
) -> bool:
    return _update_status(queue_id, ApprovalStatus.ESCALATED, reviewer_email, None, note)
