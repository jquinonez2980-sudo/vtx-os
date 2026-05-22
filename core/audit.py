"""
BigQuery audit writer.

Streams AuditRecord rows to vtx_audit.audit_log.
If BQ is unreachable (no ADC, network error, etc.) the record is printed
to stderr as JSON so no audit event is silently lost.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.base import AuditRecord

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "vtx-accounting-os-prod")
DATASET = os.environ.get("BQ_DATASET_AUDIT", "vtx_audit")
TABLE = "audit_log"
TABLE_ID = f"{PROJECT}.{DATASET}.{TABLE}"

_client = None
_client_lock = threading.Lock()


def _bq() :
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                from google.cloud import bigquery
                _client = bigquery.Client(project=PROJECT)
    return _client


def write(record: "AuditRecord") -> None:
    write_batch([record])


def write_batch(records: list["AuditRecord"]) -> None:
    if not records:
        return
    rows = [r.to_bq_row() for r in records]
    try:
        errors = _bq().insert_rows_json(TABLE_ID, rows)
        if errors:
            _fallback(rows, reason=f"BQ insert errors: {errors}")
    except Exception as exc:
        _fallback(rows, reason=str(exc))


def _fallback(rows: list[dict], reason: str) -> None:
    """Emit to stderr so audit events are never silently dropped."""
    for row in rows:
        print(
            json.dumps({"_audit_fallback": True, "_reason": reason, **row}),
            file=sys.stderr,
        )
