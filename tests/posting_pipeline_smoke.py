"""
tests/posting_pipeline_smoke.py — offline checks for the dashboard→agent posting
pipeline (Session 21). No GCP auth; nothing touches BQ or Sage.

Covers:
  1. _post_decision() — the approval contract (what may post, what must not)
  2. _build_entries() — Dr/Cr orientation, comment truncation, zero-amount skip
  3. _lid() — Sage display code → lId
  4. ClientConfig.sai_path() + registry sai_folder parsing
  5. PostRequest model — lifecycle fields, JSON-safe serialization
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
from scripts.posting_agent import _build_entries, _lid, _post_decision

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

# ── 2. entry building ───────────────────────────────────────────────────────
print("\n2. _build_entries (Dr/Cr orientation)")
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
entries = _build_entries(rows, gl_bank="1065")
check("zero-amount row skipped", len(entries) == 3)

dep = entries[0]
check("deposit: Dr bank lId",   dep["lines"][0]["account_id"] == "10650000" and dep["lines"][0]["debit"] == 1000.00)
check("deposit: Cr revenue lId", dep["lines"][1]["account_id"] == "40200000" and dep["lines"][1]["credit"] == 1000.00)

pay = entries[1]
check("payment: Dr expense lId", pay["lines"][0]["account_id"] == "55000000" and pay["lines"][0]["debit"] == 250.50)
check("payment: Cr bank lId",    pay["lines"][1]["account_id"] == "10650000" and pay["lines"][1]["credit"] == 250.50)
check("every entry balances",
      all(abs(sum(l["debit"] for l in e["lines"]) - sum(l["credit"] for l in e["lines"])) < 1e-9
          for e in entries))

trunc = entries[2]
check("comment truncated to 39 chars (Sage limit)", len(trunc["comment"]) == 39)
check("None GL falls back to suspense 5800", trunc["lines"][0]["account_id"] == "58000000")
check("queue_id carried for POSTED writeback", dep["queue_id"] == "q1")
check("dates serialized ISO", dep["date"] == "2026-01-05")

print("\n3. _lid")
check("1060 -> 10600000", _lid("1060") == "10600000")
check("1065 -> 10650000", _lid("1065") == "10650000")
check("5800 -> 58000000", _lid("5800") == "58000000")

# ── 4. registry sai_folder ──────────────────────────────────────────────────
print("\n4. ClientConfig.sai_path + registry parsing")
from core.client_registry import ClientConfig, load_registry

cfg = ClientConfig(account_no="36328934733", r_folder="Theotherapy",
                   client_id="theotherapy", gl_bank_account="1065",
                   sai_folder="Canadian Federation of theotherapy")
check("sai_path uses sai_folder + year",
      str(cfg.sai_path(2025)) == r"R:\Canadian Federation of theotherapy\2025.SAI")
cfg2 = ClientConfig(account_no="18905315443", r_folder="Concetta Enterprises Inc",
                    client_id="concetta", gl_bank_account="1060")
check("sai_path falls back to r_folder when sai_folder empty",
      str(cfg2.sai_path(2026)) == r"R:\Concetta Enterprises Inc\2026.SAI")
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

# ── result ──────────────────────────────────────────────────────────────────
total = _passed + _failed
print(f"\n{total}/{total} checks: {_passed} passed, {_failed} failed")
sys.exit(0 if _failed == 0 else 1)
