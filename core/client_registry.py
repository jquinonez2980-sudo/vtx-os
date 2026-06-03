"""
core/client_registry.py
Maps an incoming bank statement to the client it belongs to.

The routing key is the bank account number printed on the statement (parsed by
sage50.bank_statement_ocr_parser.extract_account_no). It is matched against a
maintained CSV registry that lives with the client data on the R: drive:

    R:\\bookkeeping\\client_accounts.csv

    account_no,r_folder,client_id,gl_bank_account,bank,sender_email
    1890-5315443,Concetta Enterprises Inc,concetta,1060,TD,veromendez87@hotmail.com

- account_no may be written with or without separators; it is normalized to
  digits and keyed on the FULL number (not last-4) to avoid collisions across
  the ~125 clients.
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
from dataclasses import dataclass
from pathlib import Path

from sage50.bank_statement_ocr_parser import extract_account_no

DEFAULT_REGISTRY_CSV = Path(r"R:\bookkeeping\client_accounts.csv")

_REQUIRED_COLUMNS = {"account_no", "r_folder", "client_id", "gl_bank_account"}


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

    @property
    def account_masked(self) -> str:
        """Masked form for BQ/Slack, matching existing data (e.g. 'xxxx5443')."""
        return "xxxx" + self.account_no[-4:] if self.account_no else "xxxx"


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
            f"gl_bank_account,bank,sender_email"
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
            acct = normalize_account(row.get("account_no", ""))
            if not acct:
                continue
            try:
                year_end_month = int(row.get("year_end_month") or 0)
            except (ValueError, TypeError):
                year_end_month = 0
            registry[acct] = ClientConfig(
                account_no=acct,
                r_folder=(row.get("r_folder") or "").strip(),
                client_id=(row.get("client_id") or "").strip(),
                gl_bank_account=(row.get("gl_bank_account") or "").strip(),
                bank=(row.get("bank") or "").strip(),
                sender_email=(row.get("sender_email") or "").strip(),
                year_end_month=year_end_month,
            )
    return registry


def resolve(text: str, registry: dict[str, ClientConfig]) -> ClientConfig | None:
    """Resolve OCR statement text to a ClientConfig, or None when no match."""
    acct = extract_account_no(text)
    if not acct:
        return None
    return registry.get(normalize_account(acct))
