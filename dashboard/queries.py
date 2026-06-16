"""
dashboard/queries.py — live BigQuery reads for the ops dashboard.

All reads go through the shared `core.bq_loader._bq()` singleton (ADC) and use
parameterized SQL. Money (NUMERIC → Decimal) and dates are converted to JSON-safe
values via `_jsonable`. Writes are NOT here — approvals reuse `core.approval_queue`.

Tables (PROJECT.vtx_accounting / vtx_audit), per the project schema:
    bank_transactions_categorized · gl_reconciliation · hst_returns · audit_log
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Any

from google.cloud import bigquery

from core.bq_loader import PROJECT, _bq

_ACC = f"{PROJECT}.vtx_accounting"
_AUD = f"{PROJECT}.vtx_audit"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    return value


def _rows(sql: str, params: list | None = None) -> list[dict[str, Any]]:
    cfg = bigquery.QueryJobConfig(query_parameters=params or [])
    # Real BigQuery client uses `job_config=`; the offline MockBQClient ignores it
    # (returns [] for generic SELECTs), which is the correct empty shape offline.
    result = _bq().query(sql, job_config=cfg).result()
    out: list[dict[str, Any]] = []
    for row in result:
        d = dict(row.items()) if hasattr(row, "items") else dict(row)
        out.append({k: _jsonable(v) for k, v in d.items()})
    return out


def _period_clause(field: str = "txn_date") -> str:
    return f"FORMAT_DATE('%Y-%m', {field}) = @period"


def summary(period: str, client: str | None = None) -> dict[str, Any]:
    """KPI aggregates for a period from bank_transactions_categorized + approval_queue."""
    params = [bigquery.ScalarQueryParameter("period", "STRING", period)]
    client_filter = ""
    if client:
        client_filter = " AND account_no = @client"
        params.append(bigquery.ScalarQueryParameter("client", "STRING", client))

    sql = f"""
        SELECT
            COUNT(*)                                  AS total,
            COUNTIF(needs_review)                     AS needs_review,
            COUNTIF(NOT needs_review)                 AS auto_categorized,
            COALESCE(SUM(IF(amount > 0, amount, 0)), 0)   AS deposits,
            COALESCE(SUM(IF(amount < 0, -amount, 0)), 0)  AS withdrawals
        FROM `{_ACC}.bank_transactions_categorized`
        WHERE {_period_clause()}{client_filter}
    """
    rows = _rows(sql, params)
    base = rows[0] if rows else {}
    total = int(base.get("total", 0) or 0)
    auto = int(base.get("auto_categorized", 0) or 0)
    deposits = base.get("deposits", "0")
    withdrawals = base.get("withdrawals", "0")
    net = str(Decimal(str(deposits)) - Decimal(str(withdrawals)))

    pending = 0
    prows = _rows(
        f"SELECT COUNT(*) AS n FROM `{_ACC}.approval_queue` "
        f"WHERE status = 'PENDING' AND period = @period",
        [bigquery.ScalarQueryParameter("period", "STRING", period)],
    )
    if prows:
        pending = int(prows[0].get("n", 0) or 0)

    return {
        "period": period,
        "client": client,
        "total_transactions": total,
        "auto_categorized": auto,
        "needs_review": int(base.get("needs_review", 0) or 0),
        "auto_pct": round(auto / total * 100) if total else 0,
        "deposits": str(deposits),
        "withdrawals": str(withdrawals),
        "net_movement": net,
        "pending_approvals": pending,
    }


def transactions(period: str, client: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    params = [bigquery.ScalarQueryParameter("period", "STRING", period)]
    client_filter = ""
    if client:
        client_filter = " AND account_no = @client"
        params.append(bigquery.ScalarQueryParameter("client", "STRING", client))
    limit = max(1, min(int(limit), 1000))

    sql = f"""
        SELECT txn_date, description, amount, balance, gl_account_no,
               gl_account_name, category, confidence, needs_review, bank_code
        FROM `{_ACC}.bank_transactions_categorized`
        WHERE {_period_clause()}{client_filter}
        ORDER BY txn_date ASC
        LIMIT {limit}
    """
    return _rows(sql, params)


def reconciliation(period: str) -> dict[str, Any]:
    sql = f"""
        SELECT match_status, COUNT(*) AS n
        FROM `{_ACC}.gl_reconciliation`
        WHERE period = @period
        GROUP BY match_status
    """
    rows = _rows(sql, [bigquery.ScalarQueryParameter("period", "STRING", period)])
    by_status = {r["match_status"]: int(r["n"]) for r in rows if r.get("match_status")}
    return {
        "period": period,
        "matched": by_status.get("MATCHED", 0),
        "unmatched_bank": by_status.get("UNMATCHED_BANK", 0),
        "unmatched_gl": by_status.get("UNMATCHED_GL", 0),
        "by_status": by_status,
    }


def hst(period: str) -> list[dict[str, Any]]:
    sql = f"""
        SELECT * EXCEPT (_loaded_at, _session_id)
        FROM `{_ACC}.hst_returns`
        WHERE return_period = @period
        ORDER BY line_id
    """
    return _rows(sql, [bigquery.ScalarQueryParameter("period", "STRING", period)])


def audit(limit: int = 50) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 500))
    sql = f"""
        SELECT event_ts, agent_id, event_type, status, severity, resource_type, action
        FROM `{_AUD}.audit_log`
        ORDER BY event_ts DESC
        LIMIT {limit}
    """
    return _rows(sql)


def gl_accounts(client_id: str) -> list[dict[str, Any]]:
    """Return all distinct GL accounts used by a client, sourced from BQ.

    Queries bank_transactions_categorized for every gl_account_no / gl_account_name
    pair that has appeared for this client.  This naturally grows as new transactions
    are categorized — no manual manifest required.

    Falls back to an empty list (caller should union with the static ruleset manifest)
    if BQ is unreachable or the client has no data yet.
    """
    sql = f"""
        SELECT DISTINCT gl_account_no, gl_account_name
        FROM `{_ACC}.bank_transactions_categorized`
        WHERE account_no = @client_id
          AND gl_account_no IS NOT NULL
          AND gl_account_no != ''
        ORDER BY gl_account_no
    """
    try:
        return _rows(sql, [bigquery.ScalarQueryParameter("client_id", "STRING", client_id)])
    except Exception:
        return []


def unposted(client_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Approval queue items with status=APPROVED — ready to post to Sage 50."""
    limit = max(1, min(int(limit), 1000))
    where = "status = 'APPROVED'"
    params: list = []
    if client_id:
        where += " AND account_no = @account_no"
        params.append(bigquery.ScalarQueryParameter("account_no", "STRING", client_id))
    sql = f"""
        SELECT queue_id, period, txn_date, description, amount,
               suggested_gl_no, suggested_gl_name, confidence, account_no, bank_code,
               final_gl_no, reviewer_email, reviewed_at
        FROM `{_ACC}.approval_queue`
        WHERE {where}
        ORDER BY txn_date DESC
        LIMIT {limit}
    """
    return _rows(sql, params)
