"""
tests/bq_loader_smoke.py — offline smoke tests for core/bq_loader.py

Tests ensure_table auto-migration and load_rows accurate return values
using a mock BQ client. No ADC or network required.

Run:  python tests/bq_loader_smoke.py
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Minimal Pydantic models for testing schema evolution
# ---------------------------------------------------------------------------

class _V1(BaseModel):
    description: str
    amount: Decimal

class _V2(BaseModel):
    description: str
    amount: Decimal
    payee: str | None = None       # new field added in v2
    confidence: Decimal | None = None  # second new field


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_schema_field(name: str, field_type: str = "STRING", mode: str = "NULLABLE"):
    f = MagicMock()
    f.name = name
    f.field_type = field_type
    f.mode = mode
    return f


def _inject(client):
    import core.bq_loader
    core.bq_loader._client = client


def _reset():
    import core.bq_loader
    core.bq_loader._client = None


passed = failed = 0


def check(label: str, cond: bool):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {label}")
    else:
        failed += 1
        print(f"  FAIL  {label}")


# ---------------------------------------------------------------------------
# Test 1 — ensure_table creates table when not found
# ---------------------------------------------------------------------------
print("\nTest 1 — ensure_table creates table on NotFound")

from google.cloud.exceptions import NotFound

mock_client = MagicMock()
mock_client.get_table.side_effect = NotFound("table")
_inject(mock_client)

import core.bq_loader as loader
loader.ensure_table("vtx_test", "t1", _V1)

check("create_table called once", mock_client.create_table.call_count == 1)
check("update_table NOT called (new table)", mock_client.update_table.call_count == 0)

# ---------------------------------------------------------------------------
# Test 2 — ensure_table no-ops when schema already matches
# ---------------------------------------------------------------------------
print("\nTest 2 — ensure_table no-ops when schema already matches")

mock_client = MagicMock()
existing_table = MagicMock()
# Simulate existing table has all _V1 fields + tracking columns
existing_table.schema = [
    _make_schema_field("description"),
    _make_schema_field("amount", "NUMERIC"),
    _make_schema_field("_loaded_at", "TIMESTAMP", "REQUIRED"),
    _make_schema_field("_session_id"),
]
mock_client.get_table.return_value = existing_table
_inject(mock_client)

loader.ensure_table("vtx_test", "t1", _V1)

check("create_table NOT called (already exists)", mock_client.create_table.call_count == 0)
check("update_table NOT called (no new fields)", mock_client.update_table.call_count == 0)

# ---------------------------------------------------------------------------
# Test 3 — ensure_table adds missing columns (auto-migration)
# ---------------------------------------------------------------------------
print("\nTest 3 — ensure_table auto-migrates missing columns")

mock_client = MagicMock()
existing_table = MagicMock()
# Table was created with _V1 schema — missing payee + confidence from _V2
existing_table.schema = [
    _make_schema_field("description"),
    _make_schema_field("amount", "NUMERIC"),
    _make_schema_field("_loaded_at", "TIMESTAMP", "REQUIRED"),
    _make_schema_field("_session_id"),
]
mock_client.get_table.return_value = existing_table
_inject(mock_client)

loader.ensure_table("vtx_test", "t1", _V2)

check("create_table NOT called", mock_client.create_table.call_count == 0)
check("update_table called once", mock_client.update_table.call_count == 1)

updated_table, update_fields = mock_client.update_table.call_args[0]
check("update_table target is ['schema']", update_fields == ["schema"])
new_names = {f.name for f in updated_table.schema} - {"description", "amount", "_loaded_at", "_session_id"}
check("payee added", "payee" in new_names)
check("confidence added", "confidence" in new_names)
check("no extra columns added", len(new_names) == 2)

# ---------------------------------------------------------------------------
# Test 4 — ensure_table only adds truly missing fields (partial overlap)
# ---------------------------------------------------------------------------
print("\nTest 4 — ensure_table only adds fields absent from existing schema")

mock_client = MagicMock()
existing_table = MagicMock()
# Table already has payee but not confidence
existing_table.schema = [
    _make_schema_field("description"),
    _make_schema_field("amount", "NUMERIC"),
    _make_schema_field("payee"),
    _make_schema_field("_loaded_at", "TIMESTAMP", "REQUIRED"),
    _make_schema_field("_session_id"),
]
mock_client.get_table.return_value = existing_table
_inject(mock_client)

loader.ensure_table("vtx_test", "t1", _V2)

check("update_table called once", mock_client.update_table.call_count == 1)
updated_table, _ = mock_client.update_table.call_args[0]
added = {f.name for f in updated_table.schema} - {"description", "amount", "payee", "_loaded_at", "_session_id"}
check("only confidence added (payee already present)", added == {"confidence"})

# ---------------------------------------------------------------------------
# Test 5 — load_rows returns len(rows) on full success
# ---------------------------------------------------------------------------
print("\nTest 5 — load_rows returns accurate count on success")

mock_client = MagicMock()
mock_client.insert_rows_json.return_value = []   # no errors
_inject(mock_client)

rows = [_V1(description="dep", amount=Decimal("100.00")),
        _V1(description="pay", amount=Decimal("-50.00"))]
n = loader.load_rows("vtx_test", "t1", rows, session_id="s1")

check("returns 2 on full success", n == 2)
check("insert_rows_json called once", mock_client.insert_rows_json.call_count == 1)

# ---------------------------------------------------------------------------
# Test 6 — load_rows returns 0 on total exception
# ---------------------------------------------------------------------------
print("\nTest 6 — load_rows returns 0 on BQ exception")

mock_client = MagicMock()
mock_client.insert_rows_json.side_effect = Exception("network error")
_inject(mock_client)

n = loader.load_rows("vtx_test", "t1", rows, session_id="s2")
check("returns 0 on exception", n == 0)

# ---------------------------------------------------------------------------
# Test 7 — load_rows returns n-k on partial insert failure
# ---------------------------------------------------------------------------
print("\nTest 7 — load_rows returns n-k on partial failure")

mock_client = MagicMock()
# Row at index 1 fails (schema drift on that row)
mock_client.insert_rows_json.return_value = [
    {"index": 1, "errors": [{"reason": "invalid", "message": "no column payee"}]}
]
_inject(mock_client)

n = loader.load_rows("vtx_test", "t1", rows, session_id="s3")
check("returns 1 (2 rows - 1 failed)", n == 1)

# ---------------------------------------------------------------------------
# Test 8 — load_rows returns 0 on empty input
# ---------------------------------------------------------------------------
print("\nTest 8 — load_rows returns 0 for empty list")
mock_client = MagicMock()
_inject(mock_client)
n = loader.load_rows("vtx_test", "t1", [], session_id="s4")
check("returns 0 for empty rows", n == 0)
check("insert_rows_json not called for empty input", mock_client.insert_rows_json.call_count == 0)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
total = passed + failed
print(f"\n{total}/{total} checks: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
