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
import pathlib
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dashboard.auth import require_user, reviewer_email

_STATIC = pathlib.Path(__file__).parent / "static"

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

app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


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
    # Try the local registry CSV first (works when running locally or on a machine
    # with the R: drive mounted). Falls back to BQ discovery when it's not reachable.
    try:
        from core.client_registry import load_registry
        registry = load_registry()
        configs = registry.values() if isinstance(registry, dict) else registry
        result = [
            {
                "client_id": getattr(c, "client_id", None),
                "account_masked": getattr(c, "account_masked", None),
                "bank": getattr(c, "bank", None),
                "gl_bank_account": getattr(c, "gl_bank_account", None),
            }
            for c in configs
            if getattr(c, "client_id", None)
        ]
        if result:
            return result
    except Exception:
        pass

    # Fallback: discover distinct clients from BQ approval_queue
    try:
        from core.bq_loader import PROJECT, _bq
        rows = list(
            _bq().query(
                f"SELECT DISTINCT account_no, bank_code "
                f"FROM `{PROJECT}.vtx_accounting.approval_queue` "
                f"WHERE account_no IS NOT NULL AND status != 'ARCHIVED' "
                f"ORDER BY account_no"
            ).result()
        )
        return [
            {
                "client_id": r.account_no,
                "account_masked": r.account_no,
                "bank": r.bank_code,
                "gl_bank_account": None,
                "source": "bq_discovery",
            }
            for r in rows
        ]
    except Exception:
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


# --------------------------------------------------------------------------- #
# Live ops — unposted queue
# --------------------------------------------------------------------------- #

@app.get("/api/live/unposted")
def live_unposted(
    client: str | None = None,
    limit: int = 200,
    _user: dict = Depends(require_user),
) -> list[dict[str, Any]]:
    from dashboard.queries import unposted
    return unposted(client, limit)


# --------------------------------------------------------------------------- #
# Ops — invoke backend scripts (auth required)
# --------------------------------------------------------------------------- #

@app.post("/api/ops/run-watcher")
def ops_run_watcher(
    client: str | None = None,
    period: str | None = None,
    dry_run: bool = True,
    _user: dict = Depends(require_user),
) -> dict[str, Any]:
    """Run gmail_watcher.py --once in a subprocess; returns stdout/stderr lines."""
    import subprocess
    import sys
    _ROOT = pathlib.Path(__file__).parent.parent
    args = [sys.executable, str(_ROOT / "scripts" / "gmail_watcher.py"), "--once"]
    if dry_run:
        args.append("--dry-run")
    if client:
        args += ["--client", client]
    if period:
        args += ["--period", period]
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=300,
            cwd=str(_ROOT), env={**os.environ, "PYTHONUTF8": "1"},
        )
        lines = [l for l in (result.stdout + "\n" + result.stderr).splitlines() if l.strip()]
        return {"ok": result.returncode == 0, "lines": lines, "code": result.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "lines": ["ERROR: Watcher timed out after 300s"], "code": -1}
    except Exception as exc:
        return {"ok": False, "lines": [f"ERROR: {exc}"], "code": -1}


@app.post("/api/ops/post-entries")
def ops_post_entries(
    client_id: str | None = None,
    account_no: str | None = None,
    gl_bank: str | None = None,
    _user: dict = Depends(require_user),
) -> dict[str, Any]:
    """Dry-run _post_je.py for the given client. Always dry-run — real posting requires local Sage 50 SAI access."""
    import subprocess
    import sys
    _ROOT = pathlib.Path(__file__).parent.parent
    # Resolve account_no + gl_bank from registry when not supplied directly
    if (not account_no or not gl_bank) and client_id:
        try:
            from core.client_registry import load_registry
            reg = load_registry()
            configs = list(reg.values()) if isinstance(reg, dict) else list(reg)
            cfg = next(
                (c for c in configs if getattr(c, "client_id", None) == client_id), None
            )
            if cfg:
                account_no = account_no or getattr(cfg, "account_masked", None) or getattr(cfg, "account_no", None)
                gl_bank = gl_bank or str(getattr(cfg, "gl_bank_account", "") or "")
        except Exception:
            pass
    if not account_no:
        raise HTTPException(status_code=422, detail="account_no required (or supply client_id to look up from registry)")
    if not gl_bank:
        raise HTTPException(status_code=422, detail="gl_bank required (or supply client_id to look up from registry)")
    args = [
        sys.executable, str(_ROOT / "scripts" / "_post_je.py"),
        "--account", account_no,
        "--gl-bank", gl_bank,
    ]
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=300,
            cwd=str(_ROOT), env={**os.environ, "PYTHONUTF8": "1"},
        )
        lines = [l for l in (result.stdout + "\n" + result.stderr).splitlines() if l.strip()]
        return {
            "ok": result.returncode == 0,
            "dry_run": True,
            "note": "Re-run with --commit (and Sage 50 CLOSED) to post for real.",
            "lines": lines,
            "code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "dry_run": True, "lines": ["ERROR: Post job timed out after 300s"], "code": -1}
    except Exception as exc:
        return {"ok": False, "dry_run": True, "lines": [f"ERROR: {exc}"], "code": -1}


@app.post("/api/ops/archive-client/{client_id}")
def ops_archive_client(
    client_id: str,
    _user: dict = Depends(require_user),
) -> dict[str, Any]:
    """Mark all BQ approval_queue rows for a client as ARCHIVED (removes from active queues)."""
    from google.cloud import bigquery as _bqmod
    from core.bq_loader import PROJECT, _bq
    bq = _bq()
    sql = f"""
        UPDATE `{PROJECT}.vtx_accounting.approval_queue`
        SET status = 'ARCHIVED'
        WHERE client_id = @client_id
          AND status != 'ARCHIVED'
    """
    job = bq.query(sql, job_config=_bqmod.QueryJobConfig(
        query_parameters=[_bqmod.ScalarQueryParameter("client_id", "STRING", client_id)]
    ))
    job.result()
    return {"ok": True, "client_id": client_id, "rows_archived": job.num_dml_affected_rows}


@app.post("/api/ops/onboard-client")
def ops_onboard_client(
    company_name: str,
    client_id: str,
    account_no: str,
    bank: str,
    gl_bank_account: str,
    sender_email: str = "",
    year_end_month: int = 12,
    _user: dict = Depends(require_user),
) -> dict[str, Any]:
    """Append a new client row to R:\\bookkeeping\\client_accounts.csv."""
    registry_path = pathlib.Path(r"R:\bookkeeping\client_accounts.csv")
    if not registry_path.parent.exists():
        row_csv = f"{account_no},{company_name},{client_id},{gl_bank_account},{bank},{sender_email},{year_end_month}"
        return {
            "ok": False,
            "manual": True,
            "error": "Registry path not accessible from this server.",
            "row": row_csv,
            "hint": f"Add this line to R:\\bookkeeping\\client_accounts.csv: {row_csv}",
        }
    existing = registry_path.read_text(encoding="utf-8") if registry_path.exists() else ""
    if f",{client_id}," in existing:
        raise HTTPException(status_code=409, detail=f"client_id '{client_id}' already exists in registry")
    row = f"\n{account_no},{company_name},{client_id},{gl_bank_account},{bank},{sender_email},{year_end_month}"
    with open(registry_path, "a", encoding="utf-8") as fh:
        fh.write(row)
    return {"ok": True, "client_id": client_id, "message": f"Client '{company_name}' added to registry"}
