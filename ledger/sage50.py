"""
ledger/sage50.py — Sage50Connector: the LedgerConnector for Sage 50 Canada
via Sage50Bridge.exe (sage50/bridge_reader.py).

Sage-specific behavior captured here (and nowhere else):
- GL refs are display codes; Sage wants 8-digit lIds: lid("1065") -> "10650000".
- Comments are stored truncated to 39 chars — keys use the truncated form.
- The company file is per-fiscal-year (2025.SAI, 2026.SAI); the caller picks
  the year and builds one connector per (client, year).
- Sage 50 must be CLOSED while posting (the SDK opens the .SAI exclusively).
- backup() snapshots .SAI + the companion .SAJ folder before any write.
"""
from __future__ import annotations

import shutil
from datetime import date, datetime
from pathlib import Path

from ledger.base import EntryKey, LedgerConnector, LedgerEntry, PostResult

_COMMENT_MAX = 39


def lid(display_code: str) -> str:
    """Sage 50 display code -> 8-digit lId (e.g. '1065' -> '10650000')."""
    return str(int(display_code) * 10000)


class Sage50Connector(LedgerConnector):
    platform = "sage50"

    def __init__(self, sai_path: Path | str, user: str = "sysadmin",
                 password: str | None = None):
        self.sai = Path(sai_path)
        self.user = user
        self.password = password

    # ── contract ─────────────────────────────────────────────────────────────

    def validate(self) -> None:
        if not self.sai.exists():
            raise FileNotFoundError(
                f"{self.sai} does not exist. If this is a new fiscal year, create it "
                f"in Sage 50 first: Maintenance -> Start New Year."
            )

    def key(self, entry: LedgerEntry) -> EntryKey:
        return (
            entry.entry_date.isoformat(),
            entry.comment[:_COMMENT_MAX],
            f"{entry.abs_amount:.2f}",
        )

    def existing_keys(self, start: date, end: date) -> set[EntryKey]:
        from sage50.bridge_reader import fetch_gl_transactions
        rows = fetch_gl_transactions(
            start_date=start, end_date=end,
            sai_file=str(self.sai), user=self.user, password=self.password,
        )
        return {
            (r.transaction_date.isoformat(), r.description[:_COMMENT_MAX],
             f"{max(r.debit, r.credit):.2f}")
            for r in rows
            if r.source.upper() == "BNK" and r.transaction_date is not None
        }

    def backup(self) -> Path:
        saj = self.sai.with_suffix(".SAJ")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bdir = self.sai.parent / "vtx_backup" / f"{self.sai.stem}_{stamp}"
        bdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.sai, bdir / self.sai.name)
        if saj.is_dir():
            shutil.copytree(saj, bdir / saj.name)
        return bdir

    def post(self, entries: list[LedgerEntry]) -> PostResult:
        from sage50.bridge_reader import post_journal_entries
        payload = [self._to_bridge(e) for e in entries]
        raw = post_journal_entries(
            payload, sai_file=str(self.sai), user=self.user, password=self.password,
        )
        results = [
            {"posted": bool(r.get("posted")),
             "ref": str(r.get("journal_no")) if r.get("posted") else None,
             "error": (r.get("error") or None)}
            for r in raw.get("results", [])
        ]
        return PostResult(
            posted=raw.get("posted", 0),
            errors=raw.get("errors", 0),
            results=results,
        )

    # ── Sage wire format ─────────────────────────────────────────────────────

    @staticmethod
    def _to_bridge(e: LedgerEntry) -> dict:
        c = e.comment[:_COMMENT_MAX]
        return {
            "date": e.entry_date.isoformat(),
            "source": e.source,
            "comment": c,
            "lines": [
                {"account_id": lid(l.gl_ref),
                 "debit": float(l.debit), "credit": float(l.credit),
                 "comment": c}
                for l in e.lines
            ],
        }
