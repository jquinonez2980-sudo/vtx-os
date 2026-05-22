"""
scripts/gmail_auth.py
Interactive OAuth2 setup for Gmail API.

Run once to authorize the VTX-OS application to send and read email on your behalf.
Stores the resulting credentials JSON in Secret Manager as
'vtx-gmail-oauth-credentials'.

Prerequisites:
  1. In Google Cloud Console -> APIs & Services -> OAuth consent screen:
       - App type: Internal  (or External + add your email as test user)
       - Scopes: gmail.send + gmail.modify
  2. In APIs & Services -> Credentials -> Create OAuth 2.0 Client ID:
       - Application type: Desktop app
       - Download the JSON as config/gmail_oauth_client.json

Usage:
    python scripts/gmail_auth.py
    python scripts/gmail_auth.py --client-secret config/gmail_oauth_client.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]
SECRET_NAME  = "vtx-gmail-oauth-credentials"
DEFAULT_JSON = Path("config/gmail_oauth_client.json")


def _run_oauth_flow(client_secret_file: Path) -> dict:
    """Run the browser-based OAuth2 flow and return authorized_user credentials."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret_file),
        scopes=SCOPES,
    )
    # Opens a browser window for the user to authorize.
    # prompt='consent' forces Google to show the full scope screen even for
    # previously authorized apps — required when adding new scopes to an existing token.
    creds = flow.run_local_server(port=0, open_browser=True, prompt="consent")

    # Serialize to authorized_user JSON format expected by google.oauth2.credentials
    return {
        "client_id":     creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
        "token_uri":     creds.token_uri or "https://oauth2.googleapis.com/token",
    }


def _store_secret(creds_dict: dict) -> None:
    """Store credentials JSON in Secret Manager (creates secret if absent)."""
    from google.cloud import secretmanager
    from google.api_core.exceptions import AlreadyExists, NotFound

    client  = secretmanager.SecretManagerServiceClient()
    project = "vtx-accounting-os-prod"
    parent  = f"projects/{project}"
    name    = f"{parent}/secrets/{SECRET_NAME}"

    try:
        client.create_secret(request={
            "parent":    parent,
            "secret_id": SECRET_NAME,
            "secret":    {"replication": {"automatic": {}}},
        })
        print(f"  Created secret: {name}")
    except AlreadyExists:
        pass

    payload = json.dumps(creds_dict).encode("utf-8")
    response = client.add_secret_version(
        request={"parent": name, "payload": {"data": payload}}
    )
    print(f"  Stored credentials as: {response.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gmail OAuth2 setup for VTX-OS")
    parser.add_argument(
        "--client-secret",
        default=str(DEFAULT_JSON),
        metavar="PATH",
        help=f"Path to OAuth client secret JSON (default: {DEFAULT_JSON})",
    )
    args = parser.parse_args()

    client_secret_file = Path(args.client_secret)
    if not client_secret_file.exists():
        print(f"ERROR: client secret file not found: {client_secret_file}")
        print()
        print("To create one:")
        print("  1. Go to: https://console.cloud.google.com/apis/credentials")
        print("     Project: vtx-accounting-os-prod")
        print("  2. Create OAuth 2.0 Client ID  (Desktop app)")
        print("  3. Download JSON -> save as config/gmail_oauth_client.json")
        print("  4. Run this script again")
        sys.exit(1)

    print("Opening browser for Gmail authorization...")
    print("(Authorize as jquinonez2980@gmail.com)\n")

    creds_dict = _run_oauth_flow(client_secret_file)

    print("\nAuthorization successful. Storing in Secret Manager...")
    _store_secret(creds_dict)

    print("\nGmail credentials configured.")
    print("You can now run:  python demo/monthly_close_demo.py")


if __name__ == "__main__":
    main()
