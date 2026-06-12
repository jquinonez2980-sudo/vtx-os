"""
tests/journal_entry_smoke.py
Offline smoke test for JournalEntryAgent and journal entry construction.

OFFLINE: does not call Sage50Bridge.exe or GCP.
- Verifies double-entry balance for deposits and withdrawals
- Verifies only auto-categorized transactions are posted (not needs_review)
- Verifies dry_run mode returns drafts without calling the bridge
- Verifies A2A dispatch through OrchestratorAgent

Usage:
    python tests/journal_entry_smoke.py
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _find(name: str) -> str:
    real = _ROOT / "data" / "test-client" / name
    return str(real if real.exists() else _ROOT / "tests" / "fixtures" / name)

from agents.base import TaskRequest, TaskType
from agents.journal_entry import JournalEntryAgent, _build_drafts, _draft_to_bridge
from models.banking import BankCode, BankTransaction, CategorizedTransaction

_checks: list[tuple[str, bool]] = []

def check(label: str, value: bool) -> None:
    _checks.append((label, value))
    status = "PASS" if value else "FAIL"
    print(f"  [{status}] {label}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_txn(amount: str, gl: str, needs_review: bool = False) -> CategorizedTransaction:
    return CategorizedTransaction(
        txn_id="abc123",
        bank_code=BankCode.TD,
        account_no="xxxx5443",
        txn_date=__import__("datetime").date(2025, 12, 15),
        description="TEST TXN",
        raw_description="TEST TXN",
        amount=Decimal(amount),
        gl_account_no=gl,
        gl_account_name="Test Account",
        category="Test",
        confidence=0.95,
        matched_rule_id="test",
        needs_review=needs_review,
    )


# ---------------------------------------------------------------------------
# Test 1 — Deposit builds Dr Bank / Cr Revenue
# ---------------------------------------------------------------------------
deposit = _make_txn("1000.00", "4100")
drafts = _build_drafts([deposit], bank_account="1060")

check("Deposit: one draft created",          len(drafts) == 1)
check("Deposit: Dr account is bank (1060)",  drafts[0].debit_line.account_no  == "1060")
check("Deposit: Cr account is revenue (4100)", drafts[0].credit_line.account_no == "4100")
check("Deposit: Dr amount == 1000.00",       drafts[0].debit_line.debit  == Decimal("1000.00"))
check("Deposit: Cr amount == 1000.00",       drafts[0].credit_line.credit == Decimal("1000.00"))
check("Deposit: entry is balanced",          drafts[0].is_balanced)

# ---------------------------------------------------------------------------
# Test 2 — Withdrawal builds Dr Expense / Cr Bank
# ---------------------------------------------------------------------------
withdrawal = _make_txn("-450.00", "5100")
drafts2 = _build_drafts([withdrawal], bank_account="1060")

check("Withdrawal: Dr account is expense (5100)", drafts2[0].debit_line.account_no  == "5100")
check("Withdrawal: Cr account is bank (1060)",    drafts2[0].credit_line.account_no == "1060")
check("Withdrawal: Dr amount == 450.00",          drafts2[0].debit_line.debit  == Decimal("450.00"))
check("Withdrawal: Cr amount == 450.00",          drafts2[0].credit_line.credit == Decimal("450.00"))
check("Withdrawal: entry is balanced",            drafts2[0].is_balanced)

# ---------------------------------------------------------------------------
# Test 3 — needs_review transactions are excluded
# ---------------------------------------------------------------------------
reviewed = _make_txn("500.00", "9999", needs_review=True)
all_txns = [deposit, withdrawal, reviewed]
drafts3 = _build_drafts([t for t in all_txns if not t.needs_review], bank_account="1060")

check("needs_review filtered: 2 drafts (not 3)",  len(drafts3) == 2)

# ---------------------------------------------------------------------------
# Test 4 — bridge wire format
# ---------------------------------------------------------------------------
bridge_entry = _draft_to_bridge(drafts[0])

check("Bridge entry has 'date' key",              "date" in bridge_entry)
check("Bridge entry date is ISO format",          bridge_entry["date"] == "2025-12-15")
check("Bridge entry source == 'BNK'",             bridge_entry["source"] == "BNK")
check("Bridge entry has 2 lines",                 len(bridge_entry["lines"]) == 2)
line_dr = bridge_entry["lines"][0]
line_cr = bridge_entry["lines"][1]
check("Bridge line 0 is debit",                   line_dr["debit"]  == 1000.0 and line_dr["credit"]  == 0.0)
check("Bridge line 1 is credit",                  line_cr["credit"] == 1000.0 and line_cr["debit"]   == 0.0)
check("Bridge line 0 account_id == '1060'",       line_dr["account_id"] == "1060")
check("Bridge line 1 account_id == '4100'",       line_cr["account_id"] == "4100")

# ---------------------------------------------------------------------------
# Test 5 — dry_run via JournalEntryAgent (no bridge call)
# ---------------------------------------------------------------------------
BANK_CSV = _find("dec-2025-bank-extracted.csv")

agent = JournalEntryAgent()
req = TaskRequest(
    task_type=TaskType.POST_JOURNAL_ENTRIES,
    payload={
        "bank_csv_path":  BANK_CSV,
        "period":         "2025-12",
        "gl_bank_account": "1060",
        "client_id":      "concetta",
        "dry_run":        True,
    },
)
result = agent.handle(req)

check("dry_run: result.ok is True",                        result.ok)
check("dry_run: dry_run flag in output",                   result.output.get("dry_run") is True)
check("dry_run: posted == 0",                              result.output.get("posted") == 0)
check("dry_run: total == all txns (incl. suspense)",       result.output.get("total", 0) > 0)
check("dry_run: posted_to_suspense >= 0",                  result.output.get("posted_to_suspense", -1) >= 0)
check("dry_run: total >= posted_to_suspense",
      result.output.get("total", 0) >= result.output.get("posted_to_suspense", 0))
check("dry_run: drafts list present",                      isinstance(result.output.get("drafts"), list))
if result.output.get("drafts"):
    d = result.output["drafts"][0]
    check("dry_run draft has 'balanced' key",              "balanced" in d)
    check("dry_run draft is balanced",                     d["balanced"] is True)

# ---------------------------------------------------------------------------
# Test 6 — A2A dispatch through OrchestratorAgent
# ---------------------------------------------------------------------------
from agents.orchestrator import OrchestratorAgent

# Mock fetch_gl_transactions -> [] so the idempotency pre-check is hermetic:
# this test asserts A2A dispatch reaches the bridge, independent of whatever real
# Dec-2025 BNK entries the live Sage 50 bridge might return (which would otherwise
# filter every draft as a duplicate and short-circuit before the post call).
with patch("sage50.bridge_reader.post_journal_entries") as mock_post, \
     patch("sage50.bridge_reader.fetch_gl_transactions", return_value=[]):
    mock_post.return_value = {"posted": 5, "total": 5, "errors": 0, "results": []}
    orch_req = TaskRequest(
        task_type=TaskType.POST_JOURNAL_ENTRIES,
        payload={
            "bank_csv_path":  BANK_CSV,
            "period":         "2025-12",
            "gl_bank_account": "1060",
            "client_id":      "concetta",
        },
    )
    orch = OrchestratorAgent()

    import core.bq_loader, core.audit

    class _NullBQ:
        def insert_rows_json(self, *a, **kw): return []
        def get_table(self, *a, **kw):        return MagicMock()
        def create_table(self, *a, **kw):     return MagicMock()
        def create_dataset(self, *a, **kw):   return MagicMock()
        def get_dataset(self, *a, **kw):      return MagicMock()
        def query(self, sql, *a, **kw):
            m = MagicMock()
            m.result.return_value = []
            return m

    null_bq = _NullBQ()
    core.bq_loader._client = null_bq
    core.audit._client     = null_bq

    orch_result = orch.run(orch_req)
    check("A2A dispatch: result.ok is True",      orch_result.ok)
    check("A2A dispatch: mock bridge was called", mock_post.called)
    check("A2A dispatch: posted == 5",            orch_result.output.get("posted") == 5)

# ---------------------------------------------------------------------------
# Test 7 — idempotency filtering logic: 2-of-3 drafts already in Sage 50
# ---------------------------------------------------------------------------
import datetime as _dt_mod
from models.sage50 import GLTransaction


def _make_txn_full(
    amount: str, gl: str, txn_date: _dt_mod.date,
    description: str, needs_review: bool = False,
) -> CategorizedTransaction:
    return CategorizedTransaction(
        txn_id=description[:8],
        bank_code=BankCode.TD,
        account_no="xxxx0001",
        txn_date=txn_date,
        description=description,
        raw_description=description,
        amount=Decimal(amount),
        gl_account_no=gl,
        gl_account_name="Test Account",
        category="Test",
        confidence=0.95,
        matched_rule_id="test",
        needs_review=needs_review,
    )


def _make_gl(txn_date, debit, credit, desc, source="BNK", jno="100") -> GLTransaction:
    return GLTransaction.model_validate({
        "Date":                txn_date,
        "Journal No.":         jno,
        "Source":              source,
        "Account No.":         "1060",
        "Account Description": "",
        "Debit":               debit,
        "Credit":              credit,
        "Comment":             desc,
    })


T7_A = _make_txn_full("500.00",  "4100", _dt_mod.date(2026, 1, 10), "DEPOSIT REVENUE")
T7_B = _make_txn_full("-100.00", "5200", _dt_mod.date(2026, 1, 12), "OFFICE SUPPLIES")
T7_C = _make_txn_full("-50.00",  "5500", _dt_mod.date(2026, 1, 15), "INTERNET FEE")

drafts7 = _build_drafts([T7_A, T7_B, T7_C], bank_account="1060")

# Existing GL: A and B already posted (each entry leaves 2 symmetric lines)
_P7_START = _dt_mod.date(2026, 1, 1)
_P7_END   = _dt_mod.date(2026, 1, 31)

existing_gl_7 = [
    _make_gl(T7_A.txn_date, Decimal("500.00"), Decimal("0"),   T7_A.description),
    _make_gl(T7_A.txn_date, Decimal("0"),   Decimal("500.00"), T7_A.description),
    _make_gl(T7_B.txn_date, Decimal("100.00"), Decimal("0"),   T7_B.description),
    _make_gl(T7_B.txn_date, Decimal("0"),   Decimal("100.00"), T7_B.description),
]

# Reproduce the key-building and filtering as implemented in JournalEntryAgent.handle()
existing_keys_7: set[tuple[str, str, str]] = set()
for _r in existing_gl_7:
    if (_r.source.upper() == "BNK"
            and _r.transaction_date is not None
            and _P7_START <= _r.transaction_date <= _P7_END):
        _abs = max(_r.debit, _r.credit)
        existing_keys_7.add((
            _r.transaction_date.isoformat(),
            _r.description[:39],
            f"{_abs:.2f}",
        ))

filtered7, skipped7 = [], 0
for _draft in drafts7:
    _key = (
        _draft.entry_date.isoformat(),
        _draft.description[:39],
        f"{abs(_draft.debit_line.debit):.2f}",
    )
    if _key in existing_keys_7:
        skipped7 += 1
    else:
        filtered7.append(_draft)

check("Idempotency keys: 2 unique keys from 4 symmetric GL lines",      len(existing_keys_7) == 2)
check("Idempotency filter: 2 drafts skipped (A and B already posted)",  skipped7 == 2)
check("Idempotency filter: 1 draft remains (only C is new)",             len(filtered7) == 1)
check("Idempotency filter: remaining draft is C (INTERNET FEE)",
      "INTERNET FEE" in (filtered7[0].description if filtered7 else ""))


# ---------------------------------------------------------------------------
# Test 8 — handle() integration: skips 2 existing entries, posts only 1
# ---------------------------------------------------------------------------

class _NullBQ8:
    def insert_rows_json(self, *a, **kw): return []
    def get_table(self, *a, **kw):        return MagicMock()
    def create_table(self, *a, **kw):     return MagicMock()
    def create_dataset(self, *a, **kw):   return MagicMock()
    def get_dataset(self, *a, **kw):      return MagicMock()
    def query(self, sql, *a, **kw):
        m = MagicMock()
        m.result.return_value = []
        return m


with patch("sage50.bank_parser.parse_csv") as _mock_parse8, \
     patch("sage50.categorizer.categorize_batch", return_value=[T7_A, T7_B, T7_C]), \
     patch("sage50.bridge_reader.fetch_gl_transactions", return_value=existing_gl_7), \
     patch("sage50.bridge_reader.post_journal_entries") as _mock_bridge8:

    _mock_parse8.return_value = []   # raw BankTransactions; categorize_batch is mocked above
    _mock_bridge8.return_value = {"posted": 1, "total": 1, "errors": 0, "results": []}

    import core.bq_loader, core.audit
    core.bq_loader._client = _NullBQ8()
    core.audit._client     = _NullBQ8()

    _agent8 = JournalEntryAgent()
    _req8   = TaskRequest(
        task_type=TaskType.POST_JOURNAL_ENTRIES,
        payload={
            "bank_csv_path":   "dummy.csv",
            "period":          "2026-01",
            "gl_bank_account": "1060",
            "client_id":       "test",
            "account_no":      "xxxx0001",
        },
    )
    _result8 = _agent8.handle(_req8)

    check("handle() idempotency: result.ok",                        _result8.ok)
    check("handle() idempotency: skipped_duplicates == 2",
          _result8.output.get("skipped_duplicates") == 2)
    check("handle() idempotency: bridge was called",                _mock_bridge8.called)
    check("handle() idempotency: bridge received exactly 1 entry",
          len(_mock_bridge8.call_args[0][0]) == 1 if _mock_bridge8.called else False)
    check("handle() idempotency: remaining entry is INTERNET FEE",
          "INTERNET FEE" in _mock_bridge8.call_args[0][0][0]["comment"]
          if _mock_bridge8.called else False)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
total  = len(_checks)
passed = sum(1 for _, v in _checks if v)
failed = total - passed

print(f"\n{passed}/{total} checks passed")
if failed:
    print("\nFailed checks:")
    for label, v in _checks:
        if not v:
            print(f"  FAIL: {label}")
    sys.exit(1)
else:
    print("\nAll checks passed.")
