"""
dashboard/auth.py — validate the identity-provider JWT the Orchelix app sends.

The dashboard UI lives in orchelix.com (Next.js). A signed-in user's request to
this API carries `Authorization: Bearer <JWT>` minted by the site's auth provider
(Clerk recommended; Auth0 / Supabase work identically). This module verifies that
token against the provider's public keys (JWKS) — the API never runs its own
password system.

Config via env (public identifiers, NOT secrets):
    AUTH_JWKS_URL   — provider JWKS endpoint (e.g. https://<clerk>/.well-known/jwks.json)
    AUTH_ISSUER     — expected `iss` (optional but recommended)
    AUTH_AUDIENCE   — expected `aud` (optional)

`require_user` is a FastAPI dependency: it returns the validated claims dict or
raises 401. Tests monkeypatch `_decode` (or `_jwks_client`) to stay offline.
"""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from typing import Any

import jwt
from fastapi import HTTPException, Request, status
from jwt import PyJWKClient

AUTH_JWKS_URL    = os.environ.get("AUTH_JWKS_URL", "")
AUTH_ISSUER      = os.environ.get("AUTH_ISSUER", "")
AUTH_AUDIENCE    = os.environ.get("AUTH_AUDIENCE", "")
DASHBOARD_API_KEY = os.environ.get("DASHBOARD_API_KEY", "")


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    if not AUTH_JWKS_URL:
        raise RuntimeError("AUTH_JWKS_URL is not configured")
    # PyJWKClient caches fetched keys in-process.
    return PyJWKClient(AUTH_JWKS_URL)


def _decode(token: str) -> dict[str, Any]:
    """Verify signature + claims and return the payload. Raises on any failure."""
    signing_key = _jwks_client().get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        audience=AUTH_AUDIENCE or None,
        issuer=AUTH_ISSUER or None,
        options={
            "verify_aud": bool(AUTH_AUDIENCE),
            "verify_iss": bool(AUTH_ISSUER),
        },
    )


def require_user(request: Request) -> dict[str, Any]:
    """FastAPI dependency — validate the bearer token or raise 401.

    Dev bypass: when AUTH_JWKS_URL is not configured, any non-empty Bearer token
    is accepted and the sub/email are taken from a simple base64-decoded payload
    (no signature check). This lets the dashboard work locally without a Clerk/Auth0
    setup. Never deploy to production without AUTH_JWKS_URL set.
    """
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = header[len("Bearer "):].strip()

    # ── API-key mode (no JWKS configured) ────────────────────────────────────
    if not AUTH_JWKS_URL:
        if not DASHBOARD_API_KEY:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Server not configured: set DASHBOARD_API_KEY or AUTH_JWKS_URL",
            )
        if not secrets.compare_digest(token, DASHBOARD_API_KEY):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid access key",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return {"sub": "admin", "email": "jquinonez2980@gmail.com"}
    # ── Production JWKS validation ────────────────────────────────────────────
    try:
        return _decode(token)
    except HTTPException:
        raise
    except Exception as exc:  # invalid signature / expired / wrong aud-iss / JWKS error
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def reviewer_email(claims: dict[str, Any]) -> str:
    """Best-effort human identity from JWT claims (used as the approval reviewer)."""
    return (
        claims.get("email")
        or claims.get("email_address")
        or claims.get("sub")
        or "unknown@orchelix.com"
    )
