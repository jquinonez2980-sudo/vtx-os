"""
core/client_registry.py
Maps an incoming bank statement to the client it belongs to.

The routing key is the bank account number printed on the statement, matched
against a maintained CSV registry that lives with the client data on the R: drive:

    R:\\bookkeeping\\client_accounts.csv

    account_no,r_folder,client_id,gl_bank_account,bank,sender_email,year_end_month,sai_folder,platform,platform_ref
    0000-1234567,Example Client Inc,example,1060,TD,owner@example.com,3,,sage50,

- account_no may be written with or without separators; it is normalized to
  digits and keyed on the FULL number (not last-4) to avoid collisions across
  the ~125 clients. Routing is bank-agnostic: rather than guess each bank's
  account format, resolve() searches the statement for any *registered* account
  number (see find_account_in_text) — so new banks need no code change.
- r_folder is the client's folder under R:\\ (drives the drop path + identity).
- client_id selects the categorization ruleset in BookkeepingAgent.
- gl_bank_account is the Sage GL code for the bank (reserved for journal entries).
- sender_email is an optional hint, surfaced in unrouted-mail alerts.

Override the CSV location with env VTX_CLIENT_REGISTRY (used by tests).

Usage:
    from core.client_registry import load_registry, resolve
    registry = load_registry()
    cfg = resolve(ocr_text, registry)   # ClientConfig | None
"""

from __future__ import annotations

import csv
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

DEFAULT_REGISTRY_CSV = Path(r"R:\bookkeeping\client_accounts.csv")

_REQUIRED_COLUMNS = {"account_no", "r_folder", "client_id", "gl_bank_account"}


class _RegistryRow(BaseModel):
    """Row-level validation for client_accounts.csv. Invalid rows warn + skip."""
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    account_no:      str
    r_folder:        str = ""
    client_id:       str = ""
    gl_bank_account: str = ""
    bank:            str = ""
    sender_email:    str = ""
    year_end_month:  int = 0
    sai_folder:      str = ""
    platform:        str = "sage50"
    platform_ref:    str = ""

    @field_validator("account_no")
    @classmethod
    def _normalize(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v or "")
        if not digits:
            raise ValueError("account_no contains no digits")
        return digits

    @field_validator("year_end_month", mode="before")
    @classmethod
    def _year_end(cls, v) -> int:
        try:
            n = int(v or 0)
        except (ValueError, TypeError):
            return 0
        if not (0 <= n <= 12):
            raise ValueError(f"year_end_month must be 0–12, got {n!r}")
        return n

    @field_validator("platform", mode="before")
    @classmethod
    def _platform(cls, v) -> str:
        norm = (v or "sage50").strip().lower()
        if norm not in ("sage50", "qbo"):
            print(
                f"[client_registry] unknown platform {norm!r}, defaulting to 'sage50'",
                file=sys.stderr,
            )
            return "sage50"
        return norm

    @field_validator("gl_bank_account")
    @classmethod
    def _gl_acct(cls, v: str) -> str:
        if v and not re.fullmatch(r"\d{3,6}", v):
            raise ValueError(f"gl_bank_account must be 3–6 digits, got {v!r}")
        return v


# A candidate account token in OCR text: a run of digits allowing a single
# space or dash between consecutive digits (e.g. "1890-5315443", "3632 8961-555").
# A single separator only — column gaps in reconstructed OCR rows are 2+ spaces,
# so this never merges across unrelated numbers.
_ACCT_CANDIDATE_RE = re.compile(r"\d(?:[ \-]?\d){4,}")
_MAX_TRANSIT_PREFIX = 4  # extra leading/trailing digits tolerated around a known account


def registry_path() -> Path:
    """Return the active registry CSV path (env override or default)."""
    override = os.environ.get("VTX_CLIENT_REGISTRY")
    return Path(override) if override else DEFAULT_REGISTRY_CSV


def normalize_account(raw: str) -> str:
    """Reduce an account number to digits only (e.g. '1890-5315443' -> '18905315443')."""
    return re.sub(r"\D", "", raw or "")


@dataclass(frozen=True)
class ClientConfig:
    account_no: str          # normalized full digits, the registry key
    r_folder: str
    client_id: str
    gl_bank_account: str
    bank: str = ""
    sender_email: str = ""
    year_end_month: int = 0  # 1–12 (e.g. 4 = April 30 year-end); 0 = not set
    sai_folder: str = ""     # Sage company folder under R:\ (may differ from r_folder,
                             # e.g. Theotherapy -> "Canadian Federation of theotherapy")
    platform: str = "sage50" # accounting platform: "sage50" | "qbo" (selects the
                             # LedgerConnector; see ledger/__init__.py)
    platform_ref: str = ""   # platform-specific company id — QBO realm id
                             # (printed by scripts/qbo_auth.py); empty for sage50

    def sai_path(self, year: int) -> Path:
        """Path to the Sage company file for a fiscal year: R:\\<sai_folder>\\<year>.SAI."""
        folder = self.sai_folder or self.r_folder
        return Path("R:/") / folder / f"{year}.SAI"

    @property
    def account_masked(self) -> str:
        """Masked form for BQ/Slack, matching existing data (e.g. 'xxxx5443')."""
        return "xxxx" + self.account_no[-4:] if self.account_no else "xxxx"


def resolve_client(
    account: str,
    registry: dict[str, ClientConfig] | None = None,
) -> ClientConfig | None:
    """Find a client by full account number OR masked form ('xxxx4733').

    BQ stores the masked form; the registry keys on full digits — every tool
    that bridges the two needs this exact lookup, so it lives here once.
    """
    reg = registry if registry is not None else load_registry()
    cfg = reg.get(normalize_account(account))
    if cfg:
        return cfg
    return next((c for c in reg.values() if c.account_masked == account), None)


def load_registry(path: Path | str | None = None) -> dict[str, ClientConfig]:
    """Load the client registry CSV into {normalized_account_no: ClientConfig}.

    Raises FileNotFoundError if the CSV is absent (caller decides how to surface
    it). Rows with a blank or unparseable account number are skipped.
    """
    csv_path = Path(path) if path is not None else registry_path()
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Client registry not found: {csv_path}\n"
            f"Create it with columns: account_no,r_folder,client_id,"
            f"gl_bank_account,bank,sender_email,year_end_month,sai_folder,platform,platform_ref"
        )

    registry: dict[str, ClientConfig] = {}
    with open(csv_path, "r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = _REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"{csv_path} is missing required column(s): {', '.join(sorted(missing))}"
            )
        for row in reader:
            try:
                validated = _RegistryRow.model_validate(dict(row))
            except ValidationError as exc:
                print(
                    f"[client_registry] skipping bad row {dict(row)}: {exc}",
                    file=sys.stderr,
                )
                continue
            registry[validated.account_no] = ClientConfig(
                account_no=validated.account_no,
                r_folder=validated.r_folder,
                client_id=validated.client_id,
                gl_bank_account=validated.gl_bank_account,
                bank=validated.bank,
                sender_email=validated.sender_email,
                year_end_month=validated.year_end_month,
                sai_folder=validated.sai_folder,
                platform=validated.platform,
                platform_ref=validated.platform_ref,
            )
    return registry


def find_account_in_text(
    text: str, registry: dict[str, ClientConfig]
) -> str | None:
    """Return the registered account number that appears in the statement text.

    Bank-agnostic by design: instead of guessing each bank's account format, we
    search the OCR text for any account we already know (the registry keys). The
    statement is tokenised into digit runs (single space/dash separators allowed),
    each normalised to digits, and compared to known accounts:

      - EXACT token == account  → strongest evidence (also robust on TD, where the
        account repeats on every cheque image; OCR-typo variants aren't registered
        so they simply don't match).
      - account embedded in a slightly longer token (≤ _MAX_TRANSIT_PREFIX extra
        digits) → weaker evidence, covers a printed transit/branch prefix.

    Exact evidence outranks embedded. Repeats of a SINGLE account are expected and
    fine; if two *different* registered clients appear, the statement is ambiguous
    and we return None — better to quarantine than book one client's GL into
    another. Returns the normalized account key, or None.
    """
    if not text or not registry:
        return None
    known = set(registry.keys())

    exact: set[str] = set()
    embedded: set[str] = set()
    for token in _ACCT_CANDIDATE_RE.findall(text):
        digits = normalize_account(token)
        if len(digits) < 5:
            continue
        if digits in known:
            exact.add(digits)
            continue
        for acct in known:
            if acct in digits and 0 < len(digits) - len(acct) <= _MAX_TRANSIT_PREFIX:
                embedded.add(acct)

    for distinct in (exact, embedded):  # exact evidence first
        if len(distinct) == 1:
            return next(iter(distinct))
        if len(distinct) > 1:
            return None  # two different clients present — ambiguous, quarantine
    return None


def resolve(text: str, registry: dict[str, ClientConfig]) -> ClientConfig | None:
    """Resolve OCR statement text to a ClientConfig, or None when no match."""
    acct = find_account_in_text(text, registry)
    return registry.get(acct) if acct else None


def append_registry_row(cfg: ClientConfig, path: "Path | str | None" = None) -> None:
    """Append one row to the registry CSV using csv.writer (handles quoted commas).

    Creates the file with a header row when the file does not yet exist.
    Prefer this over hand-editing to avoid quoting issues with client names
    that contain commas (e.g. "Smith, Johnson & Associates").
    """
    csv_path = Path(path) if path is not None else registry_path()
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        if write_header:
            writer.writerow([
                "account_no", "r_folder", "client_id", "gl_bank_account",
                "bank", "sender_email", "year_end_month", "sai_folder",
                "platform", "platform_ref",
            ])
        writer.writerow([
            cfg.account_no, cfg.r_folder, cfg.client_id, cfg.gl_bank_account,
            cfg.bank, cfg.sender_email, cfg.year_end_month or "",
            cfg.sai_folder, cfg.platform, cfg.platform_ref,
        ])
