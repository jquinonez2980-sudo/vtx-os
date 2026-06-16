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
    try:
        return str(int(display_code) * 10000)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"lid() requires a numeric GL display code; got {display_code!r}. "
            "Check that the GL account reference is a plain integer string "
            "(e.g. '1065'), not an alphanumeric code."
        ) from exc


def bnk_key(entry_date: date, comment: str, abs_amount) -> EntryKey:
    """THE Sage 50 BNK dedupe key — single definition for every posting path.
    Comment truncated to Sage's 39-char storage; amount as a 2dp string."""
    return (entry_date.isoformat(), comment[:_COMMENT_MAX], f"{abs_amount:.2f}")


class Sage50Connector(LedgerConnector):
    platform = "sage50"

    def __init__(self, sai_path: Path | str | None, user: str | None = None,
                 password: str | None = None):
        # sai_path=None defers resolution to bridge_reader's credential chain
        # (env VTX_SAGE50_SAI -> Secret Manager vtx-sage50-company-path).
        self.sai = Path(sai_path) if sai_path else None
        self.user = user
        self.password = password

    # ── contract ─────────────────────────────────────────────────────────────

    def validate(self) -> None:
        if self.sai is not None and not self.sai.exists():
            raise FileNotFoundError(
                f"{self.sai} does not exist. If this is a new fiscal year, create it "
                f"in Sage 50 first: Maintenance -> Start New Year."
            )

    def key(self, entry: LedgerEntry) -> EntryKey:
        return bnk_key(entry.entry_date, entry.comment, entry.abs_amount)

    def existing_keys(self, start: date, end: date) -> set[EntryKey]:
        from sage50.bridge_reader import fetch_gl_transactions
        rows = fetch_gl_transactions(
            start_date=start, end_date=end,
            sai_file=str(self.sai) if self.sai else None,
            user=self.user, password=self.password,
        )
        return {
            bnk_key(r.transaction_date, r.description, max(r.debit, r.credit))
            for r in rows
            if r.source.upper() == "BNK" and r.transaction_date is not None
        }

    def backup(self) -> Path | None:
        if self.sai is None:
            return None
        saj = self.sai.with_suffix(".SAJ")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bdir = self.sai.parent / "vtx_backup" / f"{self.sai.stem}_{stamp}"
        bdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.sai, bdir / self.sai.name)
        if saj.is_dir():
            # transient connection-manager files (not part of the books) can
            # vanish mid-copy — never let them fail a backup
            shutil.copytree(saj, bdir / saj.name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(
                                "process.pid", "*.lock", "*.tmp", "~*"))
        return bdir

    def post(self, entries: list[LedgerEntry]) -> PostResult:
        from sage50.bridge_reader import post_journal_entries
        payload = [self._to_bridge(e) for e in entries]
        raw = post_journal_entries(
            payload, sai_file=str(self.sai) if self.sai else None,
            user=self.user, password=self.password,
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
            "date"