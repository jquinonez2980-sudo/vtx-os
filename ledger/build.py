"""
ledger/build.py — platform-neutral construction of balanced bank journal
entries from categorized/approved transaction rows.

Sign convention (project-wide, inviolable):
    amount > 0  deposit   ->  Dr bank / Cr gl
    amount < 0  payment   ->  Dr gl   / Cr bank
Zero-amount rows are skipped. A missing/blank GL falls back to suspense.
Comments are passed through full-length — the connector truncates to its
platform's limit (Sage 50: 39 chars) when posting AND when computing keys.
"""
from __future__ import annotations

from decimal import Decimal

from ledger.base import LedgerEntry, LedgerLine

SUSPENSE_DEFAULT = "5800"


def build_bank_entries(
    rows: list[dict],
    bank_ref: str,
    suspense_ref: str = SUSPENSE_DEFAULT,
) -> list[LedgerEntry]:
    """rows: [{txn_date: date, description: str, amount: Decimal,
               gl: str|None, queue_id: str|None}, ...]"""
    entries: list[LedgerEntry] = []
    for r in rows:
        amt = Decimal(str(r["amount"]))
        if amt == 0:
            continue
        gl_ref = (r.get("gl") or "").strip() or suspense_ref
        absamt = abs(amt)
        desc = r.get("description") or ""
        if amt > 0:                       # deposit: Dr bank / Cr gl
            dr_ref, cr_ref = bank_ref, gl_ref
        else:                             # payment: Dr gl / Cr bank
            dr_ref, cr_ref = gl_ref, bank_ref
        entries.append(LedgerEntry(
            entry_date=r["txn_date"],
            comment=desc,
            queue_id=r.get("queue_id"),
            lines=[
                LedgerLine(gl_ref=dr_ref, debit=absamt, comment=desc),
                LedgerLine(gl_ref=cr_ref, credit=absamt, comment=desc),
            ],
        ))
    return entries
