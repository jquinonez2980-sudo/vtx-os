"""
dashboard/app.py — AcumenAI (by Orchelix) dashboard JSON API (Cloud Run).

Public:
    GET  /api/health              liveness
    GET  /api/demo/run            the baked offline showcase payload (no auth)

Authenticated (Authorization: Bearer <provider JWT>, validated in dashboard.auth):
    GET  /api/live/clients
    GET  /api/live/summary?period=&client=
    GET  /api/live/transactions?period=&client=&limit=
    GET  /api/live/reconciliation?period=
    GET  /api/live/hst?period=
    GET  /api/live/audit?limit=
    GET  /api/live/approvals
    POST /api/live/approvals/{queue_id}/{action}   action ∈ approve|reject|escalate

The UI lives in orchelix.com (Next.js); this service is API-only. CORS allows the
orchelix origin(s). Live handlers are sync `def` so FastAPI runs them in its
threadpool (BigQuery calls are blocking I/O) and the event loop never stalls.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from dashboard.auth import require_user, reviewer_email

_CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGIN", "https://orchelix.com,https://www.orchelix.com"
    ).split(",")
    if o.strip()
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bake the demo payload once at startup. build_demo_payload injects a mock,
    # captures, and resets the BQ singletons in finally — so this never leaks into
    # live mode. Guarded so a failure here can't take the service down.
    try:
        from dashboard.demo import build_demo_payload
        app.state.demo = build_demo_payload(approve=True)
    except Exception as exc:  # pragma: no cover - defensive
        app.state.demo = {"ok": False, "error": f"demo capture failed: {exc}"}
    yield


app = FastAPI(title="AcumenAI Dashboard API", version="1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=False,          # bearer tokens, not cookies
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# --------------------------------------------------------------------------- #
# Public
# --------------------------------------------------------------------------- #

@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "acumenai-dashboard-api"}


@app.get("/api/demo/run")
def demo_run() -> dict[str, Any]:
    cached = getattr(app.state, "demo", None)
    if cached:
        return cached
    from dashboard.demo import build_demo_payload
    return build_demo_payload(approve=True)


# --------------------------------------------------------------------------- #
# Authenticated — live BigQuery
# --------------------------------------------------------------------------- #

@app.get("/api/live/clients")
def live_clients(_user: dict = Depends(require_user)) -> list[dict[str, Any]]:
    try:
        from core.client_registry import load_registry
        registry = load_registry()
        configs = registry.values() if isinstance(registry, dict) else registry
        return [
            {
                "client_id": getattr(c, "client_id", None),
                "account_masked": getattr(c, "account_masked", None),
                "bank": getattr(c, "bank", None),
                "gl_bank_account": getattr(c, "gl_bank_account", None),
            }
            for c in configs
        ]
    except Exception:
        # Registry file may be absent in some environments — degrade gracefully.
        return []


@app.get("/api/live/summary")
def live_summary(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    client: str | None = None,
    _user: dict = Depends(require_user),
) -> dict[str, Any]:
    from dashboard.queries import summary
    return summary(period, client)


@app.get("/api/live/transactions")
def live_transactions(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    client: str | None = None,
    limit: int = 200,
    _user: dict = Depends(require_user),
) -> list[dict[str, Any]]:
    from dashboard.queries import transactions
    return transactions(period, client, limit)


@app.get("/api/live/reconciliation")
def live_reconciliation(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    _user: dict = Depends(require_user),
) -> dict[str, Any]:
    from dashboard.queries import reconciliation
    return reconciliation(period)


@app.get("/api/live/hst")
def live_hst(
    period: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    _user: dict = Depends(require_user),
) -> list[dict[str, Any]]:
    from dashboard.queries import hst
    return hst(period)


@app.get("/api/live/audit")
def live_audit(limit: int = 50, _user: dict = Depends(require_user)) -> list[dict[str, Any]]:
    from dashboard.queries import audit
    return audit(limit)


@app.get("/api/live/approvals")
def live_approvals(limit: int = 100, _user: dict = Depends(require_user)) -> list[dict[str, Any]]:
    from core.approval_queue import get_pending
    items = get_pending(limit=limit)
    return [it.model_dump(mode="json") for it in items]


@app.post("/api/live/approvals/{queue_id}/{action}")
def live_approval_action(
    queue_id: str,
    action: str,
    final_gl_no: str | None = None,
    note: str = "",
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    from core.approval_queue import approve, escalate, reject

    email = reviewer_email(user)
    action = action.lower()
    if action == "approve":
        if not final_gl_no:
            raise HTTPException(status_code=422, detail="final_gl_no required to approve")
        ok = approve(queue_id, reviewer_email=email, final_gl_no=final_gl_no, note=note)
    elif action == "reject":
        ok = reject(queue_id, reviewer_email=email, note=note)
    elif action == "escalate":
        ok = escalate(queue_id, reviewer_email=email, note=note)
    else:
        raise HTTPException(status_code=400, detail=f"unknown action '{action}'")

    return {"ok": bool(ok), "queue_id": queue_id, "action": action, "reviewer": email}
