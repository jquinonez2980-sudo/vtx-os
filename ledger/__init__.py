"""
ledger/ — platform-neutral GL posting layer.

    from ledger import connector_for, build_bank_entries
    conn = connector_for(client_cfg, year=2026)
    conn.validate(); conn.backup()
    new = [e for e in entries if conn.key(e) not in conn.existing_keys(lo, hi)]
    result = conn.post(new)

Platforms: sage50 (live). qbo (QuickBooks Online) is the next connector — the
registry's `platform` column selects it per client; nothing above this layer
changes when it lands.
"""
from __future__ import annotations

from ledger.base import EntryKey, LedgerConnector, LedgerEntry, LedgerLine, PostResult
from ledger.build import build_bank_entries

__all__ = [
    "EntryKey", "LedgerConnector", "LedgerEntry", "LedgerLine", "PostResult",
    "build_bank_entries", "connector_for",
]


def connector_for(cfg, year: int, *, user: str = "sysadmin",
                  password: str | None = None) -> LedgerConnector:
    """Build the right connector for a client registry entry (ClientConfig)."""
    platform = (getattr(cfg, "platform", "") or "sage50").lower()
    if platform == "sage50":
        from ledger.sage50 import Sage50Connector
        return Sage50Connector(cfg.sai_path(year), user=user, password=password)
    if platform == "qbo":
        from ledger.qbo import QboConnector
        return QboConnector(realm_id=getattr(cfg, "platform_ref", ""))
    raise ValueError(f"Unknown ledger platform '{platform}' for client {cfg.client_id!r}")
