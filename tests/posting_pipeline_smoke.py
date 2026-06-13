"""
tests/posting_pipeline_smoke.py — offline checks for the dashboard→agent posting
pipeline + the platform-neutral ledger layer. No GCP auth; nothing touches BQ
or Sage.

Covers:
  1. _post_decision() — the approval contract (what may post, what must not)
  2. ledger.build_bank_entries() — Dr/Cr orientation, zero-amount skip, suspense
  3. Sage50Connector — lid(), 39-char key truncation, bridge wire format
  4. ClientConfig.sai_path() + registry sai_folder/platform parsing
  5. PostRequest model — lifecycle fields, JSON-safe serialization
  6. connector_for() — platform routing (sage50 live, qbo not-yet, unknown rejected)
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

_passed = _failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
    else:
        _failed += 1
        print(f"  [FAIL] {name}")


# ── 1. approval contract ────────────────────────────────────────────────────
from scripts.posting_agent import _post_decision

print("\n1. _post_decision (approval contract)")
# auto-approved (needs_review=False)
check("auto-approved, no queue row -> posts",            _post_decision(False, None) is True)
check("auto-approved, PENDING row -> posts",             _post_decision(False, "PENDING") is True)
check("auto-approved, reviewer REJECTED -> held",        _post_decision(False, "REJECTED") is False)
check("auto-approved, reviewer ESCALATED -> held",       _post_decision(False, "ESCALATED") is False)
check("auto-approved, already POSTED -> never re-posts", _post_decision(False, "POSTED") is False)
# needs_review (must have explicit approval)
check("needs_review, no decision -> held",               _post_decision(True, None) is False)
check("needs_review, PENDING -> held",                   _post_decision(True, "PENDING") is False)
check("needs_review, APPROVED -> posts",                 _post_decision(True, "APPROVED") is True)
check("needs_review, REJECTED -> held",                  _post_decision(True, "REJECTED") is False)
check("needs_review, ESCALATED -> held",                 _post_decision(True, "ESCALATED") is False)
check("needs_review, POSTED -> never re-posts",          _post_decision(True, "POSTED") is False)
check("status is case-insensitive",                      _post_decision(True, "approved") is True)

# ── 2. neutral entry building ───────────────────────────────────────────────
print("\n2. ledger.build_bank_entries (Dr/Cr orientation)")
from ledger import build_bank_entries
from ledger.sage50 import Sage50Connector, lid

rows = [
    {"txn_date": date(2026, 1, 5), "description": "CLIENT DEPOSIT",
     "amount": Decimal("1000.00"), "gl": "4020", "queue_id": "q1"},
    {"txn_date": date(2026, 1, 6), "description": "HYDRO ONE PAYMENT",
     "amount": Decimal("-250.50"), "gl": "5500", "queue_id": "q2"},
    {"txn_date": date(2026, 1, 7), "description": "ZERO NOISE ROW",
     "amount": Decimal("0"), "gl": "5500", "queue_id": "q3"},
    {"txn_date": date(2026, 1, 8), "description": "X" * 60,  # over Sage's 39-char limit
     "amount": Decimal("-10.00"), "gl": None, "queue_id": "q4"},
]
entries = build_bank_entries(rows, bank_ref="1065")
check("zero-amount row skipped", len(entries) == 3)

dep, pay, trunc = entries
check("deposit: Dr bank ref",    dep.lines[0].gl_ref == "1065" and dep.lines[0].debit == Decimal("1000.00"))
check("deposit: Cr revenue ref", dep.lines[1].gl_ref == "4020" and dep.lines[1].credit == Decimal("1000.00"))
check("payment: Dr expense ref", pay.lines[0].gl_ref == "5500" and pay.lines[0].debit == Decimal("250.50"))
check("payment: Cr bank ref",    pay.lines[1].gl_ref == "1065" and pay.lines[1].credit == Decimal("250.50"))
check("every entry balances",    all(e.is_balanced() for e in entries))
check("comment NOT truncated in neutral layer", len(trunc.comment) == 60)
check("None GL falls back to suspense 5800",    trunc.lines[0].gl_ref == "5800")
check("queue_id carried for POSTED writeback",  dep.queue_id == "q1")
check("amounts stay Decimal in neutral layer",  isinstance(dep.lines[0].debit, Decimal))

# ── 3. Sage 50 connector specifics ──────────────────────────────────────────
print("\n3. Sage50Connector (lid, keys, wire format)")
check("lid 1060 -> 10600000", lid("1060") == "10600000")
check("lid 1065 -> 10650000", lid("1065") == "10650000")
check("lid 5800 -> 58000000", lid("5800") == "58000000")

conn = Sage50Connector(r"R:\nowhere\2026.SAI")
k = conn.key(trunc)
check("key truncates comment to 39 (matches Sage storage)", k[1] == "X" * 39)
check("key amount is 2dp string", k == ("2026-01-08", "X" * 39, "10.00"))

wire = Sage50Connector._to_bridge(dep)
check("wire: lId resolved",          wire["lines"][0]["account_id"] == "10650000")
check("wire: floats for the bridge", wire["lines"][0]["debit"] == 1000.00)
check("wire: ISO date + BNK source", wire["date"] == "2026-01-05" and wire["source"] == "BNK")
check("wire: comment truncated",     len(Sage50Connector._to_bridge(trunc)["comment"]) == 39)

try:
    conn.validate()
    check("validate() raises on missing .SAI", False)
except FileNotFoundError as exc:
    check("validate() raises on missing .SAI", "Start New Year" in str(exc))

# ── 4. registry sai_folder ──────────────────────────────────────────────────
print("\n4. ClientConfig.sai_path + registry parsing")
from core.client_registry import ClientConfig, load_registry

cfg = ClientConfig(account_no="36328934733", r_folder="Theotherapy",
                   client_id="theotherapy", gl_bank_account="1065",
                   sai_folder="Canadian Federation of theotherapy")
# compare .parts, not str() — separators differ between Windows and CI's Linux
check("sai_path uses sai_folder + year",
      cfg.sai_path(2025).parts[-2:] == ("Canadian Federation of theotherapy", "2025.SAI"))
cfg2 = ClientConfig(account_no="18905315443", r_folder="Concetta Enterprises Inc",
                    client_id="concetta", gl_bank_account="1060")
check("sai_path falls back to r_folder when sai_folder empty",
      cfg2.sai_path(2026).parts[-2:] == ("Concetta Enterprises Inc", "2026.SAI"))
check("account_masked", cfg.account_masked == "xxxx4733")

with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "client_accounts.csv"
    p.write_text(
        "account_no,r_folder,client_id,gl_bank_account,bank,sender_email,sai_folder\n"
        "000004733,Theotherapy,theotherapy,1065,BMO,,Canadian Federation of theotherapy\n",
        encoding="utf-8",
    )
    reg = load_registry(p)
    c = reg["000004733"]   # normalize_account keeps leading zeros (digits-only strip)
    check("registry parses sai_folder column", c.sai_folder == "Canadian Federation of theotherapy")
    check("registry parses gl_bank 1065", c.gl_bank_account == "1065")
    check("platform defaults to sage50 when column absent", c.platform == "sage50")

# ── 5. PostRequest model ────────────────────────────────────────────────────
print("\n5. PostRequest model")
from models.posting import PostRequest, PostRequestStatus

req = PostRequest(account_no="xxxx4733", period="2026-01", requested_by="a@b.c")
check("defaults to QUEUED", req.status is PostRequestStatus.QUEUED)
check("request_id generated", len(req.request_id) == 36)
d = req.model_dump(mode="json")
check("JSON-safe dump (datetime -> str)", isinstance(d["requested_at"], str))
check("lifecycle enum round-trips",
      PostRequest.model_validate({**d, "status": "DONE"}).status is PostRequestStatus.DONE)

# ── 5b. registry-enforced GL (the 1060/1065 incident guard) ─────────────────
print("\n5b. _resolve_gl + resolve_client")
from core.client_registry import resolve_client
from scripts._post_je import _resolve_gl

check("registry GL wins when CLI omitted",   _resolve_gl(None, "1065", False) == "1065")
check("matching CLI value passes through",   _resolve_gl("1065", "1065", False) == "1065")
check("CLI-only works when not in registry", _resolve_gl("1060", None, False) == "1060")
try:
    _resolve_gl("1060", "1065", False)
    check("conflicting CLI value aborts", False)
except ValueError as exc:
    check("conflicting CLI value aborts", "1060" in str(exc) and "1065" in str(exc))
check("--override-gl allows the conflict",   _resolve_gl("1060", "1065", True) == "1060")
try:
    _resolve_gl(None, None, False)
    check("no GL anywhere -> error", False)
except ValueError:
    check("no GL anywhere -> error", True)

check("resolve_client by full digits", resolve_client("000004733", reg) is c)
check("resolve_client by masked form", resolve_client("xxxx4733", reg) is c)
check("resolve_client miss -> None",   resolve_client("xxxx0000", reg) is None)

# ── 6. connector routing ────────────────────────────────────────────────────
print("\n6. connector_for (platform routing)")
from dataclasses import replace

from ledger import connector_for

c_sage = connector_for(cfg, 2026)
check("sage50 platform -> Sage50Connector", isinstance(c_sage, Sage50Connector))
check("connector gets the year's SAI path", str(c_sage.sai).endswith("2026.SAI"))

try:
    connector_for(replace(cfg, platform="qbo"), 2026)
    check("qbo without realm id -> clear error", False)
except ValueError as exc:
    check("qbo without realm id -> clear error", "platform_ref" in str(exc))

from ledger.qbo import QboConnector

c_qbo = connector_for(replace(cfg, platform="qbo", platform_ref="9341453"), 2026)
check("qbo platform -> QboConnector with realm", isinstance(c_qbo, QboConnector) and c_qbo.realm == "9341453")

try:
    connector_for(replace(cfg, platform="xero"), 2026)
    check("unknown platform rejected", False)
except ValueError:
    check("unknown platform rejected", True)

# ── 7. QboConnector offline (no network — mocked query/account map) ─────────
print("\n7. QboConnector wire format + keys (offline)")
qc = QboConnector("9341453")
qc._account_map = {"1065": "85", "4020": "91", "5800": "99"}   # mock AcctNum->Id

k = qc.key(trunc)   # the 60-char-comment entry from section 2
check("qbo key does NOT truncate the comment", k[1] == "X" * 60)
check("qbo key amount 2dp", k[2] == "10.00")

wire = qc._to_qbo(dep)
check("qbo wire: TxnDate ISO", wire["TxnDate"] == "2026-01-05")
check("qbo wire: PrivateNote carries comment", wire["PrivateNote"] == "CLIENT DEPOSIT")
check("qbo wire: debit line", wire["Line"][0]["JournalEntryLineDetail"]["PostingType"] == "Debit"
      and wire["Line"][0]["JournalEntryLineDetail"]["AccountRef"]["value"] == "85"
      and wire["Line"][0]["Amount"] == 1000.0)
check("qbo wire: credit line", wire["Line"][1]["JournalEntryLineDetail"]["PostingType"] == "Credit"
      and wire["Line"][1]["JournalEntryLineDetail"]["AccountRef"]["value"] == "91")

try:
    qc._account_id("9999")
    check("unmapped GL ref fails loudly", False)
except RuntimeError as exc:
    check("unmapped GL ref fails loudly", "AcctNum" in str(exc))

# existing_keys parses QBO JournalEntry JSON (sum of debit lines = abs amount)
qc._query = lambda q: [{                       # type: ignore[method-assign]
    "Id": "1", "TxnDate": "2026-01-05", "PrivateNote": "CLIENT DEPOSIT",
    "Line": [
        {"Amount": 1000.0, "JournalEntryLineDetail": {"PostingType": "Debit"}},
        {"Amount": 1000.0, "JournalEntryLineDetail": {"PostingType": "Credit"}},
    ],
}]
keys = qc.existing_keys(date(2026, 1, 1), date(2026, 1, 31))
check("qbo existing_keys parses JE JSON",
      keys == {("2026-01-05", "CLIENT DEPOSIT", "1000.00")})
check("qbo existing key matches connector key for same entry",
      qc.key(dep) in keys)

# ── 8. Within-batch dedupe (M1.4) ───────────────────────────────────────────
print("\n8. Within-batch dedupe")
from ledger.base import LedgerEntry, LedgerLine
from ledger.sage50 import Sage50Connector, bnk_key

_conn8 = Sage50Connector(None)

def _make_entry(d: str, desc: str, amt: float) -> LedgerEntry:
    a = Decimal(str(amt))
    return LedgerEntry(
        entry_date=date.fromisoformat(d), comment=desc,
        lines=[LedgerLine(gl_ref="1065", debit=a),
               LedgerLine(gl_ref="4020", credit=a)],
    )

e_a  = _make_entry("2026-01-10", "CLIENT PAYMENT", 500.00)
e_b  = _make_entry("2026-01-11", "HYDRO ONE",      200.00)
e_a2 = _make_entry("2026-01-10", "CLIENT PAYMENT", 500.00)   # exact duplicate of e_a

batch8 = [e_a, e_b, e_a2]
existing8: set = set()   # nothing already in ledger

seen_keys8: set = set()
deduped8 = []
for e in batch8:
    k = _conn8.key(e)
    if k not in seen_keys8:
        seen_keys8.add(k)
        deduped8.append(e)

check("within-batch dedup: 2 unique entries from 3 (one duplicate removed)", len(deduped8) == 2)
check("within-batch dedup: original entries preserved (not the duplicate)",
      deduped8[0] is e_a and deduped8[1] is e_b)
check("within-batch dedup: duplicate key correctly identified",
      _conn8.key(e_a) == _conn8.key(e_a2))

# ── 9. _fetch_postable fan-out dedup (M1.4) ─────────────────────────────────
print("\n9. _fetch_postable fan-out dedup")
from scripts.posting_agent import _STATUS_RANK  # type: ignore[attr-defined]  -- exposed for test

_rows9 = [
    # Same (date, desc, amount) matched to three queue rows: REJECTED, APPROVED, PENDING
    # The dedup must keep the APPROVED row (rank 0).
    {"txn_date": date(2026, 1, 5), "description": "CLIENT DEP", "amount": Decimal("1000"),
     "queue_status": "REJECTED", "queue_id": "q-r", "gl": "4020", "needs_review": True},
    {"txn_date": date(2026, 1, 5), "description": "CLIENT DEP", "amount": Decimal("1000"),
     "queue_status": "APPROVED", "queue_id": "q-a", "gl": "4020", "needs_review": True},
    {"txn_date": date(2026, 1, 5), "description": "CLIENT DEP", "amount": Decimal("1000"),
     "queue_status": "PENDING",  "queue_id": "q-p", "gl": "4020", "needs_review": True},
    # Distinct row — must survive unchanged
    {"txn_date": date(2026, 1, 6), "description": "HYDRO ONE", "amount": Decimal("-200"),
     "queue_status": None, "queue_id": None, "gl": "5500", "needs_review": False},
]

seen_dk9: dict = {}
for r in _rows9:
    dk = (r["txn_date"], r["description"], str(r["amount"]))
    prev = seen_dk9.get(dk)
    if prev is None:
        seen_dk9[dk] = r
    else:
        new_rank = _STATUS_RANK.get((r.get("queue_status") or "").upper(), 9)
        old_rank = _STATUS_RANK.get((prev.get("queue_status") or "").upper(), 9)
        if new_rank < old_rank:
            seen_dk9[dk] = r
deduped9 = list(seen_dk9.values())

check("fan-out dedup: 4 rows -> 2 unique (date,desc,amount) pairs", len(deduped9) == 2)
check("fan-out dedup: APPROVED row wins over REJECTED and PENDING",
      deduped9[0]["queue_id"] == "q-a")
check("fan-out dedup: distinct row preserved",
      deduped9[1]["description"] == "HYDRO ONE")

# ── result ──────────────────────────────────────────────────────────────────
total = _passed + _failed
print(f"\n{total}/{total} checks: {_passed} passed, {_failed} failed")
sys.exit(0 if _failed == 0 else 1)
