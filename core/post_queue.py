"""
core/post_queue.py

BQ-backed posting queue for the Sage 50 post workflow.

  enqueue(req)              — write a QUEUED PostRequest row to BQ
  list_recent(limit, ...)   — read recent PostRequest rows (any status)
  claim(request_id)         — mark CLAIMED (used by scripts/posting_agent.py)
  complete(request_id, ...) — mark DONE or FAILED with result detail

Table: vtx_accounting.post_requests
  Created lazily by ensure_table() on first write.
  Not partitioned (volume is low; one row per posting job).
"""

from __future__ import annotations

import datetime as _dt
import os
from typing import Any

from core.bq_loader import PROJECT, ensure_table, load_rows
from models.posting import PostRequest, PostStatus

_DATASET   = "vtx_accounting"
_TABLE     = "post_requests"
_TABLE_ID  = f"{PROJECT}.{_DATASET}.{_TABLE}"

_BQ_CFG: dict[str, Any] = {}   # no partition/cluster — low-volume table


# ---------------------------------------------------------------------------
# Internal BQ client (same lazy-singleton pattern as approval_queue.py)
# ---------------------------------------------------------------------------

_bq_client = None


def _bq():
    global _bq_client
    if _bq_client is None:
        from google.cloud import bigquery
        _bq_client = bigquery.Client(project=PROJECT)
    return _bq_client


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enqueue(req: PostRequest) -> None:
    """Write a QUEUED PostRequest row to vtx_accounting.post_requests.

    Uses a DML INSERT query job (not streaming insert) so the row is immediately
    available for UPDATE/DELETE by claim() and complete().  BQ streaming-buffer rows
    cannot be updated for up to 90 minutes — that would break the posting agent.
    """
    ensure_table(_DATASET, _TABLE, PostRequest)
    from google.cloud import bigquery
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    sql = f"""
        INSERT INTO `{_TABLE_ID}`
            (request_id, created_at, status, requested_by,
             client_id, account_no, period,
             posted_count, error_detail, claimed_at, completed_at,
             _loaded_at, _session_id)
        VALUES
            (@request_id, @created_at, @status, @requested_by,
             @client_id, @account_no, @period,
             NULL, NULL, NULL, NULL,
             @loaded_at, NULL)
    """
    params = [
        bigquery.ScalarQueryParameter("request_id",   "STRING",    req.request_id),
        bigquery.ScalarQueryParameter("created_at",   "TIMESTAMP", req.created_at.isoformat()),
        bigquery.ScalarQueryParameter("status",       "STRING",    req.status.value),
        bigquery.ScalarQueryParameter("requested_by", "STRING",    req.requested_by),
        bigquery.ScalarQueryParameter("client_id",    "STRING",    req.client_id),
        bigquery.ScalarQueryParameter("account_no",   "STRING",    req.account_no),
        bigquery.ScalarQueryParameter("period",       "STRING",    req.period),
        bigquery.ScalarQueryParameter("loaded_at",    "TIMESTAMP", now),
    ]
    _bq().query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()


def list_recent(
    limit: int = 20,
    account_no: str | None = None,
) -> list[dict[str, Any]]:
    """Return recent posting jobs (all statuses), newest first.

    Used by /api/ops/post-requests to power the Sage 50 post-history view.
    Returns JSON-safe dicts (datetimes as ISO strings).
    """
    limit = max(1, min(int(limit), 200))
    where_clauses = []
    params = []

    if account_no:
        where_clauses.append("account_no = @account_no")
        from google.cloud import bigquery
        params.append(
            bigquery.ScalarQueryParameter("account_no", "STRING", account_no)
        )

    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    sql = f"""
        SELECT
            request_id, created_at, status,
            requested_by, client_id, account_no, period,
            posted_count, error_detail, claimed_at, completed_at
        FROM `{_TABLE_ID}`
        {where}
        ORDER BY created_at DESC
        LIMIT {limit}
    """
    from google.cloud import bigquery
    cfg = bigquery.QueryJobConfig(query_parameters=params)
    try:
        result = _bq().query(sql, job_config=cfg).result()
    except Exception:
        # Table doesn't exist yet — return empty list gracefully
        return []

    out: list[dict[str, Any]] = []
    for row in result:
        d = dict(row.items()) if hasattr(row, "items") else dict(row)
        # Normalise datetime fields to ISO strings for JSON serialisation
        for k, v in d.items():
            if isinstance(v, (_dt.datetime, _dt.date)):
                d[k] = v.isoformat()
        out.append(d)
    return out


def claim(request_id: str) -> bool:
    """Mark a QUEUED job as CLAIMED (called by the local posting agent).

    Returns True if the claim succeeded (row was QUEUED and not in streaming buffer).
    Returns False — and logs a warning — if BQ rejects the UPDATE due to streaming
    buffer restrictions (row was just inserted; retry in ~90 minutes).
    Raises on any other BQ error.
    """
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    sql = f"""
        UPDATE `{_TABLE_ID}`
        SET status = 'CLAIMED', claimed_at = '{now}'
        WHERE request_id = '{request_id}'
          AND status = 'QUEUED'
    """
    try:
        _bq().query(sql).result()
        return True
    except Exception as exc:
        if "streaming buffer" in str(exc):
            import sys
            print(
                f"[post_queue] claim({request_id[:8]}): row still in BQ streaming buffer — "
                "skip this job; retry in ~90 min",
                file=sys.stderr,
            )
            return False
        raise


def complete(
    request_id: str,
    *,
    posted_count: int = 0,
    error_detail: str = "",
) -> None:
    """Mark a CLAIMED job as DONE or FAILED (called by the local posting agent)."""
    status = PostStatus.DONE.value if not error_detail else PostStatus.FAILED.value
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    error_escaped = error_detail.replace("'", "\\'")
    sql = f"""
        UPDATE `{_TABLE_ID}`
        SET status        = '{status}',
            completed_at  = '{now}',
            posted_count  = {posted_count},
            error_detail  = '{error_escaped}'
        WHERE request_id = '{request_id}'
    """
    _bq().query(sql).result()
