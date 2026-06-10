"""
scripts/qbo_auth.py — one-time interactive QuickBooks Online OAuth setup.

Mirrors scripts/gmail_auth.py: opens the Intuit consent page, captures the
redirect on localhost, exchanges the code for tokens, and stores everything in
Secret Manager as vtx-qbo-oauth. Prints the realm id — put it in the client
registry's platform_ref column.

Prereqs (Intuit developer portal — https://developer.intuit.com):
  1. Create an app (QuickBooks Online and Payments -> Accounting scope).
  2. Add redirect URI exactly:  http://localhost:8765/callback
  3. Copy the Client ID + Client Secret (Development keys = sandbox;
     Production keys after Intuit's app review).

    python scripts/qbo_auth.py --client-id <id> --client-secret <secret> [--production]
"""
from __future__ import annotations

import argparse
import json
import secrets as pysecrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

REDIRECT  = "http://localhost:8765/callback"
AUTH_URL  = "https://appcenter.intuit.com/connect/oauth2"
TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
SCOPE     = "com.intuit.quickbooks.accounting"

_result: dict = {}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        q = parse_qs(urlparse(self.path).query)
        _result["code"]  = (q.get("code") or [""])[0]
        _result["realm"] = (q.get("realmId") or [""])[0]
        _result["state"] = (q.get("state") or [""])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>AcumenAI: QuickBooks connected.</h2>"
                         b"You can close this tab and return to the terminal.")

    def log_message(self, *_):
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--client-secret", required=True)
    ap.add_argument("--production", action="store_true",
                    help="production keys (default assumes sandbox/dev keys)")
    args = ap.parse_args()

    state = pysecrets.token_urlsafe(16)
    url = AUTH_URL + "?" + urlencode({
        "client_id": args.client_id,
        "response_type": "code",
        "scope": SCOPE,
        "redirect_uri": REDIRECT,
        "state": state,
    })

    srv = HTTPServer(("localhost", 8765), _Handler)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    print("Opening the Intuit consent page — pick the (sandbox) company to connect…")
    webbrowser.open(url)
    # handle_request() returns after ONE request; wait for it
    import time
    for _ in range(600):
        if _result.get("code"):
            break
        time.sleep(0.5)
    else:
        print("ERROR: no OAuth callback received within 5 minutes.")
        return 1
    if _result.get("state") != state:
        print("ERROR: OAuth state mismatch — aborting.")
        return 1

    print("Exchanging code for tokens…")
    resp = httpx.post(
        TOKEN_URL,
        auth=(args.client_id, args.client_secret),
        data={"grant_type": "authorization_code",
              "code": _result["code"],
              "redirect_uri": REDIRECT},
        headers={"Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    tok = resp.json()

    payload = json.dumps({
        "client_id": args.client_id,
        "client_secret": args.client_secret,
        "refresh_token": tok["refresh_token"],
        "sandbox": not args.production,
    })
    # create the secret on first run; set_version only appends to existing ones
    from google.api_core.exceptions import AlreadyExists

    from core.secrets import PROJECT, _sm_client, set_version
    try:
        _sm_client().create_secret(request={
            "parent": f"projects/{PROJECT}",
            "secret_id": "vtx-qbo-oauth",
            "secret": {"replication": {"automatic": {}}},
        })
        print("Created Secret Manager secret vtx-qbo-oauth")
    except AlreadyExists:
        pass
    version = set_version("vtx-qbo-oauth", payload)
    print(f"\nStored credentials in Secret Manager: vtx-qbo-oauth ({version})")
    print(f"Realm ID (company): {_result['realm']}")
    print("\nNext steps:")
    print(f"  1. Put the realm id in the client registry: platform=qbo, "
          f"platform_ref={_result['realm']}")
    print("  2. Verify connectivity:")
    print(f"     python -c \"from ledger.qbo import QboConnector; "
          f"QboConnector('{_result['realm']}').validate(); print('QBO OK')\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
