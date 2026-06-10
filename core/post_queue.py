"""
Post-request queue — BQ-backed handoff between the dashboard ("Post to Sage"
button on Cloud Run) and the local posting agent (scripts/posting_agent.py,
which runs on the Windows machine where Sage 50 + the bridge live).

All writes use DML (INSERT/UPDATE queries), never streaming inserts: rows in
the streaming buffer cannot be UPDATEd for up to 90 minutes, and the agent
must claim a QUEUED row seconds after the dashboard creates it.
"""

from __future__ import annotations

import sys

from google.cloud import bigquery

from core.bq_loader import PROJECT, _bq, ensure_table
from models.posting import PostRequest, PostRequestStatus

DATASET  = "vtx_accounting"
TABLE    = "post_requests"
TABLE_ID = f"{PROJECT}.{DATASET}.{TABLE}"


def _ensure() -> None:
    ensure_table(DATASET, TABLE, PostRequest)


def enqueue(req: PostRequest) -> PostRequest:
    """Insert a QUEUED post request via DML. Returns the request."""
    _ensure()
    sql = f"""
        INSERT INTO `{TABLE_ID}`
            (request_id, requested_at, requested_by, client_id, account_no,
             period, status, posted, skipped, errors, result_note)
        VALUES
            (@request_id, CURRENT_TIMESTAMP(), @requested_by, @client_id,
             @account_no, @period, @status, 0, 0, 0, "")
    """
    params = [
        bigquery.ScalarQueryParameter("request_id",   "STRING", req.request_id),
        bigquery.ScalarQueryParameter("requested_by", "STRING", req.requested_by),
        bigquery.ScalarQueryParameter("client_id",    "STRING", req.client_id),
        bigquery.ScalarQueryParameter("account_no",   "STRING", req.account_no),
        bigquery.ScalarQueryParameter("period",       "STRING", req.period),
        bigquery.ScalarQueryParameter("status",       "STRING", req.status.value),
    ]
    _bq().query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
    return req


def fetch_queued(limit: int = 10) -> list[PostRequest]:
    """Oldest-first QUEUED requests."""
    _ensure()
    sql = f"""
        SELECT * FROM `{TABLE_ID}`
        WHERE status = 'QUEUED'
        ORDER BY requested_at ASC
        LIMIT {int(limit)}
    """
    rows = list(_bq().query(sql).result())
    return [PostRequest.model_validate(dict(r.items())) for r in rows]


def list_recent(limit: int = 20, account_no: str | None = None) -> list[dict]:
    """Recent requests (any status), newest first — for the dashboard view."""
    _ensure()
    where = "TRUE"
    params: list = []
    if account_no:
        where = "account_no = @account_no"
        params.append(bigquery.ScalarQueryParameter("account_no", "STRING", account_no))
    sql = f"""
        SELECT * FROM `{TABLE_ID}`
        WHERE {where}
        ORDER BY requested_at DESC
        LIMIT {int(limit)}
    """
    cfg = bigquery.QueryJobConfig(query_parameters=params) if params else None
    rows = list(_bq().query(sql, job_config=cfg).result())
    return [PostRequest.model_validate(dict(r.items())).model_dump(mode="json") for r in rows]


def claim(request_id: str) -> bool:
    """QUEUED -> RUNNING. Returns False if someone else claimed it first."""
    sql = f"""
        UPDATE `{TABLE_ID}`
        SET status = 'RUNNING', started_at = CURRENT_TIMESTAMP()
        WHERE request_id = @request_id AND status = 'QUEUED'
    """
    job = _bq().query(sql, job_config=bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("request_id", "STRING", request_id)]))
    job.result()
    return bool(job.num_dml_affected_rows)


def complete(
    request_id: str,
    status: PostRequestStatus,
    posted: int = 0,
    skipped: int = 0,
    errors: int = 0,
    note: str = "",
) -> bool:
    """RUNNING -> DONE | FAILED with result counts."""
    sql = f"""
        UPDATE `{TABLE_ID}`
        SET status = @status, completed_at = CURRENT_TIMESTAMP(),
            posted = @posted, skipped = @skipped, errors = @errors,
            result_note = @note
        WHERE request_id = @request_id
    """
    params = [
        bigquery.ScalarQueryParameter("status",     "STRING", status.value),
        bigquery.ScalarQueryParameter("posted",     "INT64",  posted),
        bigquery.ScalarQueryParameter("skipped",    "INT64",  skipped),
        bigquery.ScalarQueryParameter("errors",     "INT64",  errors),
        bigquery.ScalarQueryParameter("note",       "STRING", note[:1000]),
        bigquery.ScalarQueryParameter("request_id", "STRING", request_id),
    ]
    try:
        _bq().query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()
        return True
    except Exception as exc:
        print(f"[post_queue] complete() failed for {request_id}: {exc}", file=sys.stderr)
        return False
