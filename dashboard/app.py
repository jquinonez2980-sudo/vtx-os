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

from fastapi import Depends, FastAPI, HTTPException, Query, Request
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

def _load_clients_meta() -> dict:
    """Load display metadata from clients_meta.json (account_masked → display fields)."""
    try:
        import json as _json
        _meta_path = pathlib.Path(__file__).parent / "clients_meta.json"
        if _meta_path.exists():
            return _json.loads(_meta_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


# --------------------------------------------------------------------------- #
# Public — early-access signup (landing page lead capture)
# --------------------------------------------------------------------------- #

_SIGNUP_BUCKET: dict[str, list[float]] = {}   # naive per-instance rate limit


@app.post("/api/signup")
def public_signup(
    email: str,
    name: str = "",
    firm: str = "",
    clients: str = "",
    website: str = "",          # honeypot — real users never fill this
    request: Request = None,
) -> dict[str, Any]:
    """PUBLIC lead capture for the landing page. No auth by design; defended by
    honeypot + per-IP rate limit + strict validation. Writes via DML."""
    import re as _re
    import time as _time

    if website:                                  # bot filled the honeypot
        return {"ok": True}                      # pretend success, store nothing
    email = email.strip().lower()[:200]
    if not _re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", email):
        raise HTTPException(status_code=422, detail="Enter a valid email address")

    ip = (request.client.host if request and request.client else "?")
    now = _time.time()
    hits = [t for t in _SIGNUP_BUCKET.get(ip, []) if now - t < 3600]
    if len(hits) >= 5:
        raise HTTPException(status_code=429, detail="Too many signups — try later")
    _SIGNUP_BUCKET[ip] = hits + [now]

    from google.cloud import bigquery as _bqm

    from core.bq_loader import PROJECT, _bq, ensure_table
    from models.signup import EarlySignup

    rec = EarlySignup(email=email, name=name.strip()[:120],
                      firm=firm.strip()[:160], clients=clients.strip()[:40])
    ensure_table("vtx_accounting", "early_access_signups", EarlySignup)
    _bq().query(
        f"INSERT INTO `{PROJECT}.vtx_accounting.early_access_signups` "
        "(signup_id, created_at, name, email, firm, clients, source) "
        "VALUES (@i, CURRENT_TIMESTAMP(), @n, @e, @f, @c, 'landing')",
        job_config=_bqm.QueryJobConfig(query_parameters=[
            _bqm.ScalarQueryParameter("i", "STRING", rec.signup_id),
            _bqm.ScalarQueryParameter("n", "STRING", rec.name),
            _bqm.ScalarQueryParameter("e", "STRING", rec.email),
            _bqm.ScalarQueryParameter("f", "STRING", rec.firm),
            _bqm.ScalarQueryParameter("c", "STRING", rec.clients),
        ]),
    ).result()
    return {"ok": True, "message": "You're on the list — we'll be in touch."}


@app.get("/api/live/user")
def live_user(user: dict = Depends(require_user)) -> dict[str, Any]:
    """Return the authenticated user's identity from token claims."""
    import re as _re
    email = user.get("email") or user.get("sub") or "unknown"
    raw = user.get("name") or ""
    if not raw or raw == email:
        local = email.split("@")[0] if "@" in email else email
        # jquinonez2980 → Jorge Quinonez style not possible without real name
        # Strip digits, split on delimiters, title-case
        clean = _re.sub(r"\d+", "", local).replace(".", " ").replace("_", " ").replace("-", " ").strip()
        raw = clean.title() if clean else "Admin"
    return {"email": email, "name": raw, "sub": user.get("sub")}


@app.get("/api/live/clients")
def live_clients(_user: dict = Depends(require_user)) -> list[dict[str, Any]]:
    meta = _load_clients_meta()

    def _enrich(d: dict) -> dict:
        mask = d.get("account_masked") or ""
        m = meta.get(mask, {})
        if not d.get("company_name"):
            d["company_name"] = m.get("company_name") or d.get("client_id") or mask
        if not d.get("industry"):
            d["industry"] = m.get("industry", "")
        # Bank from clients_meta.json overrides BQ-discovered bank_code
        if m.get("bank"):
            d["bank"] = m["bank"]
        return d

    # Try the local registry CSV first (works with R: drive mounted locally).
    try:
        from core.client_registry import load_registry
        registry = load_registry()
        configs = registry.values() if isinstance(registry, dict) else registry
        seen: dict[str, dict] = {}
        for c in configs:
            if not getattr(c, "client_id", None):
                continue
            mask = getattr(c, "account_masked", None) or ""
            if mask in seen:
                continue
            seen[mask] = _enrich({
                "client_id": getattr(c, "client_id", None),
                "company_name": getattr(c, "r_folder", None) or getattr(c, "client_id", None),
                "account_masked": mask,
                "bank": getattr(c, "bank", None),
                "gl_bank_account": str(getattr(c, "gl_bank_account", "") or ""),
                "industry": "",
            })
        if seen:
            return list(seen.values())
    except Exception:
        pass

    # Fallback: discover distinct clients from BQ approval_queue, deduplicated.
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
        seen = {}
        for r in rows:
            acct = r.account_no
            if acct in seen:
                continue
            m = meta.get(acct, {})
            seen[acct] = {
                "client_id": acct,
                "company_name": m.get("company_name", acct),
                "account_masked": acct,
                "bank": r.bank_code,
                "gl_bank_account": None,
                "industry": m.get("industry", ""),
                "source": "bq_discovery",
            }
        return list(seen.values())
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
def live_approvals(
    limit: int = 500,
    account_no: str | None = None,
    period: str | None = None,
    _user: dict = Depends(require_user),
) -> list[dict[str, Any]]:
    from core.approval_queue import get_pending
    # account_no may be comma-separated for multi-account companies (e.g. Theotherapy BMO + RBC)
    accts = [a.strip() for a in account_no.split(",") if a.strip()] if account_no else None
    items = get_pending(limit=limit, account_nos=accts, period=period)
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
    dry_run: bool = False,
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


@app.post("/api/ops/queue-post")
def ops_queue_post(
    account_no: str,
    period: str = "",
    client_id: str = "",
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    """Queue a Sage 50 posting job for the local posting agent.

    Cloud Run cannot reach Sage 50 (Windows-only bridge + R:\\ company files), so
    the dashboard writes a QUEUED request to BQ; scripts/posting_agent.py --watch
    running on the bookkeeping machine claims and executes it.
    """
    from core.post_queue import enqueue
    from models.posting import PostRequest
    req = PostRequest(
        requested_by=reviewer_email(user),
        client_id=client_id,
        account_no=account_no,
        period=period,
    )
    enqueue(req)
    return {
        "ok": True,
        "request_id": req.request_id,
        "account_no": account_no,
        "period": period,
        "note": "Queued. The local posting agent will post when Sage 50 is closed.",
    }


@app.get("/api/ops/post-requests")
def ops_post_requests(
    account_no: str | None = None,
    limit: int = 20,
    _user: dict = Depends(require_user),
) -> list[dict[str, Any]]:
    """Recent posting jobs (any status) — drives the Sage 50 post-history view."""
    from core.post_queue import list_recent
    return list_recent(limit=limit, account_no=account_no)


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


@app.post("/api/ops/archive-client/{account_no}")
def ops_archive_client(
    account_no: str,
    _user: dict = Depends(require_user),
) -> dict[str, Any]:
    """Archive pending/approved rows for an account. POSTED rows are skipped —
    they have already been written to Sage 50 and must not re-enter the queue."""
    from google.cloud import bigquery as _bqmod
    from core.bq_loader import PROJECT, _bq
    bq = _bq()
    sql = f"""
        UPDATE `{PROJECT}.vtx_accounting.approval_queue`
        SET status = 'ARCHIVED',
            review_note = CONCAT(
                '[archived:', status, ']',
                IF(IFNULL(review_note, '') != '', CONCAT(' ', review_note), '')
            )
        WHERE account_no = @account_no
          AND status NOT IN ('ARCHIVED', 'POSTED')
    """
    job = bq.query(sql, job_config=_bqmod.QueryJobConfig(
        query_parameters=[_bqmod.ScalarQueryParameter("account_no", "STRING", account_no)]
    ))
    job.result()
    return {"ok": True, "account_no": account_no, "rows_archived": job.num_dml_affected_rows}


@app.post("/api/ops/restore-client/{account_no}")
def ops_restore_client(
    account_no: str,
    _user: dict = Depends(require_user),
) -> dict[str, Any]:
    """Restore ARCHIVED rows, recovering their prior status from review_note.
    Rows that were POSTED before archival remain ARCHIVED — they cannot re-enter
    the queue without risking a double-post to Sage 50."""
    from google.cloud import bigquery as _bqmod
    from core.bq_loader import PROJECT, _bq
    bq = _bq()
    sql = f"""
        UPDATE `{PROJECT}.vtx_accounting.approval_queue`
        SET
            status = COALESCE(
                NULLIF(REGEXP_EXTRACT(IFNULL(review_note, ''), r'^\[archived:([A-Z]+)\]'), 'POSTED'),
                'PENDING'
            ),
            review_note = NULLIF(
                TRIM(REGEXP_REPLACE(IFNULL(review_note, ''), r'^\[archived:[A-Z]+\] ?', '')),
                ''
            )
        WHERE account_no = @account_no
          AND status = 'ARCHIVED'
          AND NOT REGEXP_CONTAINS(IFNULL(review_note, ''), r'^\[archived:POSTED\]')
    """
    job = bq.query(sql, job_config=_bqmod.QueryJobConfig(
        query_parameters=[_bqmod.ScalarQueryParameter("account_no", "STRING", account_no)]
    ))
    job.result()
    return {"ok": True, "account_no": account_no,
            "rows_restored": job.num_dml_affected_rows}


@app.get("/api/auth/gmail-status")
def gmail_auth_status(_user: dict = Depends(require_user)) -> dict[str, Any]:
    """Check whether Gmail OAuth credentials are stored in Secret Manager or locally."""
    # Check Secret Manager first
    try:
        from core.secrets import get
        val = get("vtx-gmail-oauth-credentials")
        if val and len(val) > 10:
            return {"connected": True, "source": "secret_manager"}
    except Exception:
        pass
    # Check local config file
    local = pathlib.Path(__file__).parent.parent / "config" / "gmail_credentials.json"
    if local.exists():
        return {"connected": True, "source": "local_file"}
    return {"connected": False, "source": "none",
            "hint": "Run: python scripts/gmail_auth.py — then re-check status"}


@app.post("/api/ops/run-hst")
def ops_run_hst(
    client: str | None = None,
    period: str | None = None,
    _user: dict = Depends(require_user),
) -> dict[str, Any]:
    """Trigger PrepareHSTReturnAgent via the orchestrator for a client/period."""
    import subprocess
    import sys
    _ROOT = pathlib.Path(__file__).parent.parent
    script = _ROOT / "scripts" / "_run_hst.py"
    if not script.exists():
        return {
            "ok": False,
            "lines": [
                "HST agent script not found at scripts/_run_hst.py.",
                "Run locally: python -c \"from agents.prepare_hst_return import PrepareHSTReturnAgent; ...\"",
                f"Or query BQ directly: SELECT * FROM vtx_accounting.hst_returns WHERE return_period = '{period or 'YYYY-MM'}'",
            ],
            "hint": "The HST agent requires Vertex AI + BQ access. Run from the project root.",
        }
    args = [sys.executable, str(script)]
    if client:
        args += ["--client", client]
    if period:
        args += ["--period", period]
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=120,
            cwd=str(_ROOT), env={**os.environ, "PYTHONUTF8": "1"},
        )
        lines = [l for l in (result.stdout + "\n" + result.stderr).splitlines() if l.strip()]
        return {"ok": result.returncode == 0, "lines": lines, "code": result.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "lines": ["ERROR: HST agent timed out after 120s"], "code": -1}
    except Exception as exc:
        return {"ok": False, "lines": [f"ERROR: {exc}"], "code": -1}


@app.post("/api/ops/onboard-client")
def ops_onboard_client(
    company_name: str,
    client_id: str,
    account_no: str,
    bank: str,
    gl_bank_account: str,
    sender_email: str = "",
    year_end_month: int = 12,
    sai_folder: str = "",
    industry: str = "",
    _user: dict = Depends(require_user),
) -> dict[str, Any]:
    """Append a new client row to the registry CSV (full 9-column width)."""
    import json as _json
    import re as _re

    # Derive masked account for clients_meta.json key
    digits = _re.sub(r"\D", "", account_no)
    mask = "xxxx" + digits[-4:] if len(digits) >= 4 else account_no

    # Always write to clients_meta.json — baked into the image so it survives
    # container restarts after the next deploy.
    _meta_path = pathlib.Path(__file__).parent / "clients_meta.json"
    try:
        meta = _json.loads(_meta_path.read_text(encoding="utf-8")) if _meta_path.exists() else {}
        meta[mask] = {
            "company_name": company_name,
            "industry": industry,
            "bank": bank,
        }
        _meta_path.write_text(_json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    # Use the env-var registry path (works in Cloud Run); fall back to R: drive if set.
    import os as _os
    registry_path = pathlib.Path(
        _os.environ.get("VTX_CLIENT_REGISTRY", r"R:\bookkeeping\client_accounts.csv")
    )
    # Full 9-column row — matches core/client_registry.py's schema. A short row
    # silently zeroes year_end_month and breaks sai_path() (incident class:
    # Theotherapy folder mismatch).
    row_csv = (f"{account_no},{company_name},{client_id},{gl_bank_account},"
               f"{bank},{sender_email},{year_end_month},"
               f"{sai_folder or company_name},sage50")
    header = ("account_no,r_folder,client_id,gl_bank_account,bank,"
              "sender_email,year_end_month,sai_folder,platform\n")
    try:
        existing = registry_path.read_text(encoding="utf-8") if registry_path.exists() else header
        if f",{client_id}," in existing:
            raise HTTPException(status_code=409, detail=f"client_id '{client_id}' already exists in registry")
        with open(registry_path, "a", encoding="utf-8") as fh:
            fh.write("\n" + row_csv)
        return {
            "ok": True,
            "client_id": client_id,
            "row": row_csv,
            "message": (f"Client '{company_name}' added to the container registry. "
                        f"IMPORTANT: also append this row to "
                        f"R:\\bookkeeping\\client_accounts.csv (and commit "
                        f"config/client_accounts.csv) or it is lost on redeploy."),
        }
    except HTTPException:
        raise
    except Exception as exc:
        return {
            "ok": True,
            "manual_registry": True,
            "client_id": client_id,
            "message": f"Client '{company_name}' saved to dashboard metadata. Registry write failed: {exc}",
            "row": row_csv,
        }
