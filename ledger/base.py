"""
ledger/base.py — platform-neutral journal-entry types + the LedgerConnector
contract every accounting platform implements (Sage 50 today, QuickBooks
Online next).

Design rules:
- Entries are built platform-neutrally (ledger/build.py) with Decimal amounts
  and *display-level* GL references (e.g. "1065"); each connector resolves
  refs to its own ids (Sage lId, QBO account id) at post time.
- Dedupe keys are computed BY THE CONNECTOR, because the key must match what
  that platform actually stored (Sage truncates comments to 39 chars; QBO
  doesn't). Key shape everywhere: (date_iso, stored_comment, abs_amount_2dp).
- Comments are NOT truncated in the neutral layer.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

EntryKey = tuple[str, str, str]   # (date_iso, comment_as_stored, abs_amount_2dp)


@dataclass(frozen=True)
class LedgerLine:
    gl_ref: str                    # platform-agnostic account ref, e.g. "1065"
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    comment: str = ""


@dataclass
class LedgerEntry:
    entry_date: date
    comment: str
    lines: list[LedgerLine]
    source: str = "BNK"
    queue_id: str | None = None    # approval_queue linkage for POSTED writeback

    def is_balanced(self) -> bool:
        return sum(l.debit for l in self.lines) == sum(l.credit for l in self.lines)

    @property
    def abs_amount(self) -> Decimal:
        return max(sum(l.debit for l in self.lines), sum(l.credit for l in self.lines))


@dataclass
class PostResult:
    posted: int = 0
    errors: int = 0
    # one per input entry, in order: {"posted": bool, "ref": str|None, "error": str|None}
    results: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.errors == 0


class LedgerConnector(ABC):
    """One per (client, accounting platform, period-scope). Stateless between calls."""

    platform: str = "abstract"

    @abstractmethod
    def validate(self) -> None:
        """Raise with a clear, actionable message if posting cannot proceed
        (missing company file, unreachable API, expired OAuth, ...)."""

    @abstractmethod
    def existing_keys(self, start: date, end: date) -> set[EntryKey]:
        """Keys of entries already in the ledger for the range — the dedupe set."""

    @abstractmethod
    def key(self, entry: LedgerEntry) -> EntryKey:
        """The key THIS platform would store for the entry (handles its own
        comment truncation/normalisation) — must match existing_keys() output."""

    @abstractmethod
    def post(self, entries: list[LedgerEntry]) -> PostResult:
        """Post balanced entries; results align 1:1 with the input order."""

    def backup(self) -> Path | None:
        """Snapshot the ledger before posting. File-based platforms override;
        API platforms (QBO) return None — the provider holds the history."""
        return None
