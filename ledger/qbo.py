"""
ledger/qbo.py — QboConnector: the LedgerConnector for QuickBooks Online.

Unlike Sage 50 there is no local company file and no bridge process — QBO is a
REST API, so this connector also works from Cloud Run (QBO clients need no
local posting agent).

Auth: OAuth2 refresh-token flow. Credentials live in Secret Manager as JSON:

    vtx-qbo-oauth = {"client_id": "...", "client_secret": "...",
                     "refresh_token": "...", "sandbox": true|false}

IMPORTANT — Intuit ROTATES the refresh token on every refresh. The new token is
persisted back to Secret Manager immediately; losing it means re-running
scripts/qbo_auth.py. Local override: VTX_SECRET_VTX_QBO_OAUTH (rotation then
cannot be persisted — fine for one-off dev runs only).

Account mapping: our GL refs are 4-digit display codes ("1065"). QBO accounts
carry an optional AcctNum field — clients migrated from Sage keep their
numbering there. The connector loads AcctNum -> QBO Account.Id once per
instance; a ref with no matching AcctNum fails loudly (fix the QBO chart of
accounts, do not guess).

Dedupe key: (date_iso, PrivateNote, abs_amount_2dp) — QBO stores long memos,
so unlike Sage there is NO comment truncation in the key.
"""
from __future__ import annotations

import json
import time
from datetime import date
from decimal import Decimal

import httpx

from ledger.base import EntryKey, LedgerConnector, LedgerEntry, PostResult

SECRET_NAME   = "vtx-qbo-oauth"
_TOKEN_URL    = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
_BASE_PROD    = "https://quickbooks.api.intuit.com"
_BASE_SANDBOX = "https://sandbox-quickbooks.api.intuit.com"
_MINOR        = "75"           # API minorversion
_PAGE         = 1000           # query pagination size (QBO max)

# access tokens are valid ~60 min; refresh 5 min early. Cached per realm.
_token_cache: dict[str, tuple[str, float]] = {}


def _load_creds() -> dict:
    from core.secrets import get
    return json.loads(get(SECRET_NAME))


def _persist_rotated_refresh_token(creds: dict, new_refresh: str) -> None:
    if new_refresh == creds.get("refresh_token"):
        return
    creds["refresh_token"] = new_refresh
    try:
        from core.secrets import set_version
        set_version(SECRET_NAME, json.dumps(creds))
    except Exception as exc:                       # env-var override path
        import sys
        print(f"[qbo] WARNING: rotated refresh token NOT persisted ({exc}). "
              f"Next run may need scripts/qbo_auth.py again.", file=sys.stderr)


def _access_token(realm_id: str) -> tuple[str, str]:
    """Returns (access_token, base_url). Refreshes + persists rotation as needed."""
    cached = _token_cache.get(realm_id)
    if cached and cached[1] > time.time():
        creds = _load_creds()
        return cached[0], _BASE_SANDBOX if creds.get("sandbox") else _BASE_PROD
    creds = _load_creds()
    resp = httpx.post(
        _TOKEN_URL,
        auth=(creds["client_id"], creds["client_secret"]),
        data={"grant_type": "refresh_token",
              "refresh_token": creds["refresh_token"]},
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    tok = resp.json()
    _persist_rotated_refresh_token(creds, tok.get("refresh_token", ""))
    access = tok["access_token"]
    _token_cache[realm_id] = (access, time.time() + tok.get("expires_in", 3600) - 300)
    return access, _BASE_SANDBOX if creds.get("sandbox") else _BASE_PROD


class QboConnector(LedgerConnector):
    platform = "qbo"

    def __init__(self, realm_id: str):
        if not realm_id:
            raise ValueError(
                "QBO client has no realm id — set platform_ref in the client "
                "registry (printed by scripts/qbo_auth.py during authorization)."
            )
        self.realm = realm_id
        self._account_map: dict[str, str] | None = None   # AcctNum -> QBO Id

    # ── HTTP plumbing ────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, **kw) -> dict:
        token, base = _access_token(self.realm)
        url = f"{base}/v3/company/{self.realm}/{path}"
        params = {"minorversion": _MINOR, **kw.pop("params", {})}
        resp = httpx.request(
            method, url, params=params,
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/json",
                     "Content-Type": "application/json"},
            timeout=60, **kw,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"QBO {method} {path} -> {resp.status_code}: "
                               f"{resp.text[:500]}")
        return resp.json()

    def _query(self, q: str) -> list[dict]:
        """Run a QBO SQL-ish query, following pagination to exhaustion."""
        out: list[dict] = []
        start = 1
        while True:
            page_q = f"{q} STARTPOSITION {start} MAXRESULTS {_PAGE}"
            data = self._request("GET", "query", params={"query": page_q})
            resp = data.get("QueryResponse", {})
            rows = next((v for k, v in resp.items()
                         if isinstance(v, list)), [])
            out.extend(rows)
            if len(rows) < _PAGE:
                return out
            start += _PAGE

    # ── account resolution ───────────────────────────────────────────────────

    def account_map(self) -> dict[str, str]:
        if self._account_map is None:
            rows = self._query("SELECT Id, AcctNum, Name FROM Account")
            self._account_map = {
                r["AcctNum"]: r["Id"] for r in rows if r.get("AcctNum")
            }
        return self._account_map

    def _account_id(self, gl_ref: str) -> str:
        amap = self.account_map()
        if gl_ref not in amap:
            raise RuntimeError(
                f"GL ref '{gl_ref}' has no QBO account with that AcctNum in realm "
                f"{self.realm}. Set the Number field on the account in the QBO "
                f"chart of accounts (it should mirror the Sage code)."
            )
        return amap[gl_ref]

    # ── contract ─────────────────────────────────────────────────────────────

    def validate(self) -> None:
        # proves: creds load, refresh works, realm reachable, accounts numbered
        amap = self.account_map()
        if not amap:
            raise RuntimeError(
                f"QBO realm {self.realm} reachable but NO accounts have AcctNum "
                f"set — populate account numbers before posting."
            )

    def key(self, entry: LedgerEntry) -> EntryKey:
        # no truncation — QBO PrivateNote holds the full comment
        return (entry.entry_date.isoformat(), entry.comment,
                f"{entry.abs_amount:.2f}")

    def existing_keys(self, start: date, end: date) -> set[EntryKey]:
        rows = self._query(
            "SELECT Id, TxnDate, PrivateNote, Line FROM JournalEntry "
            f"WHERE TxnDate >= '{start.isoformat()}' "
            f"AND TxnDate <= '{end.isoformat()}'"
        )
        keys: set[EntryKey] = set()
        for r in rows:
            amt = Decimal("0")
            for line in r.get("Line", []):
                detail = line.get("JournalEntryLineDetail", {})
                if detail.get("PostingType") == "Debit":
                    amt += Decimal(str(line.get("Amount", 0)))
            keys.add((r.get("TxnDate", ""), r.get("PrivateNote", "") or "",
                      f"{amt:.2f}"))
        return keys

    def post(self, entries: list[LedgerEntry]) -> PostResult:
        result = PostResult()
        for e in entries:
            try:
                data = self._request("POST", "journalentry",
                                     json=self._to_qbo(e))
                je = data.get("JournalEntry", {})
                result.posted += 1
                result.results.append(
                    {"posted": True, "ref": str(je.get("Id", "")), "error": None})
            except Exception as exc:
                result.errors += 1
                result.results.append(
                    {"posted": False, "ref": None, "error": str(exc)[:300]})
        return result

    # ── QBO wire format ──────────────────────────────────────────────────────

    def _to_qbo(self, e: LedgerEntry) -> dict:
        lines = []
        for i, l in enumerate(e.lines):
            posting = "Debit" if l.debit > 0 else "Credit"
            amount = l.debit if l.debit > 0 else l.credit
            lines.append({
                "Id": str(i),
                "DetailType": "JournalEntryLineDetail",
                "Amount": float(amount),
                "Description": l.comment or e.comment,
                "JournalEntryLineDetail": {
                    "PostingType": posting,
                    "AccountRef": {"value": self._account_id(l.gl_ref)},
                },
            })
        return {
            "TxnDate": e.entry_date.isoformat(),
            "PrivateNote": e.comment,
            "Line": lines,
        }
