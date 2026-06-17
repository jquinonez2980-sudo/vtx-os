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
    """Write a QUEUED PostRequest row to vtx_accounting.post_requests."""
    ensure_table(PostRequest, _DATASET, _TABLE)
    rows = [req.model_dump(mode="json")]
    inserted = load_rows(rows, _DATASET, _TABLE)
    if inserted == 0:
        raise RuntimeError(
            f"post_queue.enqueue: BQ insert returned 0 for request_id={req.request_id}"
        )


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


def claim(request_id: str) -> None:
    """Mark a QUEUED job as CLAIMED (called by the local posting agent)."""
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    sql = f"""
        UPDATE `{_TABLE_ID}`
        SET status = 'CLAIMED', claimed_at = '{now}'
        WHERE request_id = '{request_id}'
          AND status = 'QUEUED'
    """
    _bq().query(sql).result()


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
