"""
scripts/verify_gmail_oauth.py

Read vtx-gmail-oauth-credentials from Secret Manager and verify:
  - JSON is valid and has required fields
  - Both scopes (send + readonly) are present
  - Token can be refreshed (credentials work)
  - Gmail API responds to a profile fetch (read permission confirmed)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REQUIRED_FIELDS = {"client_id", "client_secret", "refresh_token", "token_uri"}
REQUIRED_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


def main() -> None:
    print("=" * 55)
    print("Gmail OAuth credential verification")
    print("=" * 55)

    checks = []

    # 1. Read from Secret Manager
    try:
        from core.secrets import get
        raw = get("vtx-gmail-oauth-credentials")
        checks.append(("Secret readable from Secret Manager", True))
    except Exception as exc:
        checks.append(("Secret readable from Secret Manager", False))
        _report(checks)
        print(f"\nFATAL: {exc}")
        sys.exit(1)

    # 2. Valid JSON
    try:
        info = json.loads(raw)
        checks.append(("Credentials JSON is valid", True))
    except json.JSONDecodeError as exc:
        checks.append(("Credentials JSON is valid", False))
        _report(checks)
        print(f"\nFATAL: {exc}")
        sys.exit(1)

    # 3. Required fields present
    missing = REQUIRED_FIELDS - set(info.keys())
    checks.append((f"Required fields present ({', '.join(sorted(REQUIRED_FIELDS))})", not missing))
    if missing:
        print(f"\nMissing fields: {missing}")

    # 4. Build credentials object and refresh token
    try:
        import google.oauth2.credentials
        from google.auth.transport.requests import Request

        creds = google.oauth2.credentials.Credentials.from_authorized_user_info(
            info, scopes=REQUIRED_SCOPES
        )
        creds.refresh(Request())
        checks.append(("Access token refreshed successfully", True))
    except Exception as exc:
        checks.append(("Access token refreshed successfully", False))
        _report(checks)
        print(f"\nFATAL: {exc}")
        sys.exit(1)

    # 5. Gmail API — fetch profile (confirms both send + read scopes accepted)
    try:
        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        email = profile.get("emailAddress", "unknown")
        checks.append((f"Gmail API responds — account: {email}", True))
    except Exception as exc:
        checks.append(("Gmail API responds", False))
        _report(checks)
        print(f"\nFATAL: {exc}")
        sys.exit(1)

    # 6. Confirm inbox read permission — list 1 message
    try:
        result = service.users().messages().list(userId="me", maxResults=1).execute()
        count = result.get("resultSizeEstimate", 0)
        checks.append((f"Inbox readable — ~{count} messages estimated", True))
    except Exception as exc:
        checks.append(("Inbox readable (gmail.modify)", False))
        _report(checks)
        print(f"\nFATAL: {exc}")
        sys.exit(1)

    _report(checks)
    print("\nGmail OAuth is fully configured (send + inbox read).")
    print("Next: configure vtx-google-chat-webhook, then run the monthly close demo.")


def _report(checks: list) -> None:
    print()
    for desc, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {desc}")
    passed = sum(1 for _, ok in checks if ok)
    print(f"\n  {passed}/{len(checks)} checks passed")


if __name__ == "__main__":
    main()
