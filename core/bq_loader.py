"""
BigQuery loader — schema derivation from Pydantic models + streaming insert.

Usage:
    from core.bq_loader import ensure_table, load_rows
    from models.sage50 import GLTransaction

    ensure_table("vtx_accounting", "gl_transactions", GLTransaction,
                 partition_field="transaction_date", cluster_fields=["account_no"])
    load_rows("vtx_accounting", "gl_transactions", rows, session_id="abc-123")
"""

from __future__ import annotations

import os
import sys
import types
import typing
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "vtx-accounting-os-prod")

# Pydantic field type → BigQuery column type
_TYPE_MAP: dict[Any, str] = {
    str:      "STRING",
    int:      "INTEGER",
    float:    "FLOAT64",
    Decimal:  "NUMERIC",
    bool:     "BOOL",
    date:     "DATE",
    datetime: "TIMESTAMP",
    dict:     "JSON",
}

# Tracking columns appended to every vtx_accounting table row
_TRACKING_FIELDS = [
    ("_loaded_at", "TIMESTAMP", "REQUIRED"),
    ("_session_id", "STRING",   "NULLABLE"),
]

_client = None


def _bq():
    global _client
    if _client is None:
        from google.cloud import bigquery
        _client = bigquery.Client(project=PROJECT)
    return _client


# ---------------------------------------------------------------------------
# Schema derivation
# ---------------------------------------------------------------------------

def _unwrap(annotation: Any) -> tuple[Any, bool]:
    """Return (inner_type, is_nullable) unwrapping X | None / Optional[X]."""
    # Python 3.10+ union: int | None  → types.UnionType
    if isinstance(annotation, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return (args[0] if args else str), True
    # typing.Optional[X] = typing.Union[X, None]
    if typing.get_origin(annotation) is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return (args[0] if args else str), True
    return annotation, False


def schema_from_model(model_class: type[BaseModel]):
    """Derive a list of bigquery.SchemaField from a Pydantic model class.

    Supports list[T] fields: mapped to T REPEATED (e.g. list[float] -> FLOAT64 REPEATED).
    All scalar fields are NULLABLE (Sage 50 exports can be sparse).
    """
    from google.cloud import bigquery

    fields = []
    for field_name, field_info in model_class.model_fields.items():
        inner, _nullable = _unwrap(field_info.annotation)

        # Handle list[T] -> T REPEATED (needed for embedding vectors, etc.)
        if typing.get_origin(inner) is list:
            args = typing.get_args(inner)
            item_type = args[0] if args else str
            bq_item_type = _TYPE_MAP.get(item_type, "STRING")
            fields.append(bigquery.SchemaField(field_name, bq_item_type, mode="REPEATED"))
            continue

        bq_type = _TYPE_MAP.get(inner, "STRING")
        fields.append(bigquery.SchemaField(field_name, bq_type, mode="NULLABLE"))

    for col_name, col_type, col_mode in _TRACKING_FIELDS:
        fields.append(bigquery.SchemaField(col_name, col_type, mode=col_mode))

    return fields


# ---------------------------------------------------------------------------
# Dataset + Table management
# ---------------------------------------------------------------------------

def ensure_dataset(dataset_id: str) -> str:
    """Create the BQ dataset if it does not exist. Returns the full dataset ID."""
    from google.cloud import bigquery

    client = _bq()
    full_id = f"{PROJECT}.{dataset_id}"
    dataset = bigquery.Dataset(full_id)
    dataset.location = os.environ.get("BQ_LOCATION", "northamerica-northeast2")
    client.create_dataset(dataset, exists_ok=True)
    return full_id


def ensure_table(
    dataset: str,
    table_name: str,
    model_class: type[BaseModel],
    partition_field: str | None = None,
    cluster_fields: list[str] | None = None,
) -> str:
    """Create the BQ table if it does not exist. Returns the full table ID."""
    from google.cloud import bigquery
    from google.cloud.exceptions import NotFound

    table_id = f"{PROJECT}.{dataset}.{table_name}"
    client = _bq()

    try:
        client.get_table(table_id)
        return table_id
    except NotFound:
        pass

    schema = schema_from_model(model_class)
    table = bigquery.Table(table_id, schema=schema)

    if partition_field:
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY,
            field=partition_field,
        )

    if cluster_fields:
        table.clustering_fields = cluster_fields

    client.create_table(table)
    return table_id


# ---------------------------------------------------------------------------
# Row serialisation
# ---------------------------------------------------------------------------

def _serialise(value: Any) -> Any:
    """Make a single field value JSON-safe for BQ streaming insert."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)          # BQ NUMERIC accepts "123.45"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()   # "YYYY-MM-DD"
    if isinstance(value, dict):
        import json
        return json.dumps(value)
    return value


def _to_bq_dict(
    model: BaseModel,
    session_id: str | None = None,
) -> dict[str, Any]:
    raw = model.model_dump()
    row: dict[str, Any] = {k: _serialise(v) for k, v in raw.items()}
    row["_loaded_at"] = datetime.now(timezone.utc).isoformat()
    row["_session_id"] = session_id
    return row


# ---------------------------------------------------------------------------
# Streaming insert
# ---------------------------------------------------------------------------

def load_rows(
    dataset: str,
    table_name: str,
    rows: list[BaseModel],
    session_id: str | None = None,
) -> int:
    """Stream rows into a BQ table. Returns count of rows inserted.

    Falls back to stderr JSON if BQ is unreachable (no ADC, network, etc.).
    """
    if not rows:
        return 0

    table_id = f"{PROJECT}.{dataset}.{table_name}"
    bq_rows = [_to_bq_dict(r, session_id) for r in rows]

    try:
        errors = _bq().insert_rows_json(table_id, bq_rows)
        if errors:
            _fallback(table_id, bq_rows, reason=f"insert errors: {errors}")
    except Exception as exc:
        _fallback(table_id, bq_rows, reason=str(exc))

    return len(rows)


def _fallback(table_id: str, rows: list[dict], reason: str) -> None:
    import json
    for row in rows:
        print(
            json.dumps({"_bq_fallback": True, "_table": table_id, "_reason": reason, **row}),
            file=sys.stderr,
        )
