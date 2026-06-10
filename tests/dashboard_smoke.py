"""
tests/dashboard_smoke.py — offline checks for the AcumenAI showcase demo artifact.

Phase A of the dashboard: dashboard.demo.build_demo_payload() captures the offline
bookkeeping pipeline as a JSON-safe dict that the orchelix.com showcase page animates.
These checks guard the payload's shape, the pipeline's headline numbers, and — most
importantly — the safety contract that the capture resets the BigQuery singletons so
nothing leaks into live mode.

OFFLINE: MockBQClient + mocked httpx; no GCP, no network, no auth.

Run:  python tests/dashboard_smoke.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

_passed = _failed = 0
_MONEY_RE = re.compile(r"^\d+\.\d{2}$")


def check(label: str, cond: bool, note: str = "") -> None:
    global _passed, _failed
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {note}" if note else ""))
    if cond:
        _passed += 1
    else:
        _failed += 1


def _bq_singletons_clear() -> bool:
    """True iff all injected BQ singletons are reset (the capture's safety contract)."""
    import core.approval_queue
    import core.audit
    import core.bq_loader
    return (core.bq_loader._client is None
            and core.audit._client is None
            and core.approval_queue._bq_client is None)


def _api_checks() -> None:
    """FastAPI TestClient checks for the Phase B API — auth gate + read shapes.

    Offline: a real RSA keypair signs a test JWT, the JWKS lookup is faked, and a
    MockBQClient is injected AFTER lifespan startup (the startup demo-capture resets
    the BQ singletons, so injecting earlier would be clobbered).
    """
    import jwt as pyjwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fastapi.testclient import TestClient

    import dashboard.auth as auth_mod
    from dashboard.app import app
    from tests.p1_7_e2e import MockBQClient, _inject_mock

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub = key.public_key()
    token = pyjwt.encode({"sub": "u-1", "email": "cpa@orchelix.com"}, key, algorithm="RS256")

    class _FakeKey:
        def __init__(self, k):
            self.key = k

    class _FakeJWKS:
        def get_signing_key_from_jwt(self, _token):
            return _FakeKey(pub)

    auth_mod._jwks_client = lambda: _FakeJWKS()  # bypass the network JWKS fetch
    # require_user only enters the JWKS branch when AUTH_JWKS_URL is set; without
    # it (and without DASHBOARD_API_KEY) the server correctly answers 503 — so the
    # test must mark JWKS as "configured" for the 401/200 paths to be reachable.
    auth_mod.AUTH_JWKS_URL = "https://test.invalid/.well-known/jwks.json"
    hdr = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        _inject_mock(MockBQClient())  # after startup reset

        check("GET /api/health -> ok", client.get("/api/health").json().get("status") == "ok")
        demo = client.get("/api/demo/run")
        check("GET /api/demo/run -> 200 + five beats",
              demo.status_code == 200 and "ingest" in demo.json().get("beats", {}))

        # Auth gate
        check("live/summary no token -> 401",
              client.get("/api/live/summary", params={"period": "2025-12"}).status_code == 401)
        check("live/summary bad token -> 401",
              client.get("/api/live/summary", params={"period": "2025-12"},
                         headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401)

        # Authed reads (mock BQ -> empty/zero shapes, 200)
        s = client.get("/api/live/summary", params={"period": "2025-12"}, headers=hdr)
        check("live/summary with token -> 200 + keys",
              s.status_code == 200
              and {"total_transactions", "net_movement", "pending_approvals"}.issubset(s.json()))
        check("live/summary bad period -> 422",
              client.get("/api/live/summary", params={"period": "nope"}, headers=hdr).status_code == 422)

        for path in ("transactions", "hst", "audit", "approvals"):
            params = {} if path in ("audit", "approvals") else {"period": "2025-12"}
            resp = client.get(f"/api/live/{path}", params=params, headers=hdr)
            check(f"live/{path} -> 200 list", resp.status_code == 200 and isinstance(resp.json(), list))

        r = client.get("/api/live/reconciliation", params={"period": "2025-12"}, headers=hdr)
        check("live/reconciliation -> 200 + matched key", r.status_code == 200 and "matched" in r.json())

        cl = client.get("/api/live/clients", headers=hdr)
        check("live/clients -> 200 list (graceful if registry absent)",
              cl.status_code == 200 and isinstance(cl.json(), list))

        act = client.post("/api/live/approvals/q-test/approve", params={"final_gl_no": "4100"}, headers=hdr)
        check("POST approve -> 200 ok + reviewer from JWT",
              act.status_code == 200 and act.json().get("ok") is True
              and act.json().get("reviewer") == "cpa@orchelix.com")
        check("POST approve without final_gl_no -> 422",
              client.post("/api/live/approvals/q-test/approve", headers=hdr).status_code == 422)

    # Restore singletons so nothing leaks into later code.
    import core.approval_queue
    import core.audit
    import core.bq_loader
    core.bq_loader._client = None
    core.audit._client = None
    core.approval_queue._bq_client = None


def main() -> int:
    from dashboard.demo import build_demo_payload

    payload = build_demo_payload(approve=True)

    # --- Safety contract: capture must leave no mock injected -----------------
    check("BQ singletons reset to None after capture", _bq_singletons_clear(),
          "live mode must re-create the real client")

    # --- Top-level shape ------------------------------------------------------
    check("payload ok is True", payload.get("ok") is True)
    check("brand is 'AcumenAI by Orchelix'", payload.get("brand") == "AcumenAI by Orchelix")
    for key in ("client", "period", "generated_at", "beats", "recap"):
        check(f"top-level key present: {key}", key in payload)

    beats = payload.get("beats", {})
    for beat in ("ingest", "verify", "categorize", "audit", "approve"):
        check(f"beat present: {beat}", beat in beats)

    # --- Beat 1: ingest -------------------------------------------------------
    ingest = beats.get("ingest", {})
    check("ingest: 20 transactions", ingest.get("transactions") == 20,
          str(ingest.get("transactions")))
    check("ingest: bank auto-detected (TD)", ingest.get("bank_code") == "TD")
    check("ingest: money_in is 2-decimal string",
          isinstance(ingest.get("money_in"), str) and bool(_MONEY_RE.match(ingest["money_in"])))
    check("ingest: money_out is 2-decimal string",
          isinstance(ingest.get("money_out"), str) and bool(_MONEY_RE.match(ingest["money_out"])))
    check("ingest: deposits + payments == transactions",
          ingest.get("deposits", 0) + ingest.get("payments", 0) == ingest.get("transactions"))

    # --- Beat 2: verify (the moat) -------------------------------------------
    verify = beats.get("verify", {})
    check("verify: chain total > 0", verify.get("total", 0) > 0)
    check("verify: fully reconciled (reconciled == total)",
          verify.get("reconciled") == verify.get("total"),
          f"{verify.get('reconciled')}/{verify.get('total')}")
    check("verify: all_reconciled flag True", verify.get("all_reconciled") is True)

    # --- Beat 3: categorize ---------------------------------------------------
    cat = beats.get("categorize", {})
    check("categorize: auto + review == total",
          cat.get("auto_categorized", 0) + cat.get("needs_review", 0) == cat.get("total"))
    check("categorize: queued == needs_review",
          cat.get("queued") == cat.get("needs_review"))
    check("categorize: auto_pct in 0..100",
          isinstance(cat.get("auto_pct"), int) and 0 <= cat["auto_pct"] <= 100)
    check("categorize: chat_notified True", cat.get("chat_notified") is True)

    # --- Beat 4: audit --------------------------------------------------------
    audit = beats.get("audit", {})
    check("audit: event_count > 0", audit.get("event_count", 0) > 0)
    check("audit: AGENT_START + TASK_COMPLETE recorded",
          {"AGENT_START", "TASK_COMPLETE"}.issubset(set(audit.get("event_types", []))))

    # --- Beat 5: approve ------------------------------------------------------
    appr = beats.get("approve", {})
    check("approve: one item cleared (after == before - 1)",
          appr.get("pending_after") == appr.get("pending_before", 0) - 1,
          f"{appr.get('pending_before')} -> {appr.get('pending_after')}")
    check("approve: approved item captured",
          isinstance(appr.get("approved"), dict) and bool(appr["approved"].get("description")))

    # --- Recap ----------------------------------------------------------------
    recap = payload.get("recap", {})
    check("recap: duration_ms is a non-negative int",
          isinstance(recap.get("duration_ms"), int) and recap["duration_ms"] >= 0)
    check("recap: headline non-empty", bool(recap.get("headline")))

    # --- approve=False omits the beat ----------------------------------------
    no_approve = build_demo_payload(approve=False)
    check("approve=False omits the approve beat",
          "approve" not in no_approve.get("beats", {}))
    check("BQ singletons reset after approve=False run too", _bq_singletons_clear())

    # --- JSON serializable (no Decimal/date leakage) -------------------------
    try:
        json.dumps(payload)
        serializable = True
    except TypeError:
        serializable = False
    check("payload is JSON-serializable (no raw Decimal/date)", serializable)

    # --- Committed artifact (if present) matches the shape -------------------
    artifact = _ROOT / "demo" / "demo_run.json"
    if artifact.exists():
        baked = json.loads(artifact.read_text(encoding="utf-8"))
        check("baked demo/demo_run.json has all five beats",
              {"ingest", "verify", "categorize", "audit", "approve"}.issubset(
                  set(baked.get("beats", {}).keys())))
    else:
        check("baked demo/demo_run.json present (run scripts/export_demo_json.py)",
              False, "artifact missing")

    # ------------------------------------------------------------------ #
    # Phase B API checks (FastAPI TestClient, offline)                     #
    # ------------------------------------------------------------------ #
    _api_checks()

    total = _passed + _failed
    print(f"\n{total}/{total} checks: {_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
