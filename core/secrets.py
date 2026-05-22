"""
Secret Manager client with in-process caching and local-dev env-var fallback.

Usage:
    from core.secrets import get, get_sage50_odbc_conn

    conn_str = get_sage50_odbc_conn()          # production
    raw      = get("vtx-sage50-odbc-conn")     # generic accessor

Local dev: set the env var VTX_SECRET_<UPPER_NAME> to bypass SM entirely.
Example:   VTX_SECRET_VTX_SAGE50_ODBC_CONN=DSN=LocalSage50;UID=admin;PWD=dev
"""

from __future__ import annotations

import os
import threading
from typing import Any

from google.cloud import secretmanager

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "vtx-accounting-os-prod")

# Known secret names — single source of truth
SAGE50_ODBC_CONN = "vtx-sage50-odbc-conn"
SAGE50_COMPANY_PATH = "vtx-sage50-company-path"
SAGE50_PASSWORD = "vtx-sage50-password"
CANTAX_API_KEY = "vtx-cantax-api-key"
GMAIL_OAUTH_CREDENTIALS = "vtx-gmail-oauth-credentials"

_cache: dict[str, str] = {}
_lock = threading.Lock()
_client: secretmanager.SecretManagerServiceClient | None = None


def _sm_client() -> secretmanager.SecretManagerServiceClient:
    global _client
    if _client is None:
        _client = secretmanager.SecretManagerServiceClient()
    return _client


def _env_key(secret_name: str) -> str:
    """Convert a secret name to its local-dev env var override key."""
    return "VTX_SECRET_" + secret_name.upper().replace("-", "_")


def get(secret_name: str, version: str = "latest") -> str:
    """Return the secret value as a string.

    Checks (in order):
      1. In-process cache
      2. Local env var override (VTX_SECRET_<UPPER_NAME>)
      3. GCP Secret Manager

    Raises ValueError if the value is a placeholder that was never set.
    """
    cache_key = f"{secret_name}@{version}"

    with _lock:
        if cache_key in _cache:
            return _cache[cache_key]

    env_override = os.environ.get(_env_key(secret_name))
    if env_override:
        with _lock:
            _cache[cache_key] = env_override
        return env_override

    name = f"projects/{PROJECT}/secrets/{secret_name}/versions/{version}"
    response = _sm_client().access_secret_version(request={"name": name})
    value = response.payload.data.decode("utf-8")

    if value.startswith("PLACEHOLDER"):
        raise ValueError(
            f"Secret '{secret_name}' has not been set. "
            f"Update it with: gcloud secrets versions add {secret_name} --data-file=-"
        )

    with _lock:
        _cache[cache_key] = value
    return value


def set_version(secret_name: str, value: str) -> str:
    """Add a new version to an existing secret. Returns the new version name."""
    parent = f"projects/{PROJECT}/secrets/{secret_name}"
    response = _sm_client().add_secret_version(
        request={"parent": parent, "payload": {"data": value.encode("utf-8")}}
    )
    _invalidate(secret_name)
    return response.name


def _invalidate(secret_name: str) -> None:
    """Remove all cached versions for a secret (call after rotating)."""
    with _lock:
        stale = [k for k in _cache if k.startswith(f"{secret_name}@")]
        for k in stale:
            del _cache[k]


def clear_cache() -> None:
    """Flush the entire in-process cache (useful in tests)."""
    with _lock:
        _cache.clear()


# ---------------------------------------------------------------------------
# Typed accessors — one per secret, documents expected format
# ---------------------------------------------------------------------------

def get_sage50_odbc_conn() -> str:
    """ODBC connection string for Sage 50 Canada.

    Expected format:
        DSN=Sage50Company;UID=sysadmin;PWD=yourpassword
    or DSN-less:
        Driver={Sage 50 ODBC Driver};CompanyDatabase=C:\\path\\to\\company.sai;UID=sysadmin;PWD=yourpassword
    """
    return get(SAGE50_ODBC_CONN)


def get_sage50_company_path() -> str:
    """Windows path to the Sage 50 company data file (.sai).

    Expected format:  C:\\Sage\\Simply\\Data\\CompanyName.sai
    """
    return get(SAGE50_COMPANY_PATH)


def get_sage50_password() -> str:
    """Sage 50 user password for SDK bridge authentication."""
    return get(SAGE50_PASSWORD)


def get_cantax_api_key() -> str:
    """Cantax API key for T1/T2 tax return integration."""
    return get(CANTAX_API_KEY)


def get_gmail_oauth_credentials() -> dict[str, Any]:
    """Gmail OAuth2 credentials JSON (from Google Cloud Console → OAuth 2.0 Client).

    Returns the parsed JSON dict, ready for google-auth-oauthlib.
    """
    import json
    return json.loads(get(GMAIL_OAUTH_CREDENTIALS))
