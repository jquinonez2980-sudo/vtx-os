# PROJECT_STATUS.md — AcumenAI (vtx-os)
# Updated: 2026-06-13  |  Session: 24  (Fable 5 audit M0–M2)
# Full change history: docs/SESSION_LOG.md

---

## CURRENT PHASE

**Phase 2 complete.**  Post-Phase-2 hardening + Fable 5 audit improvements (Sessions 21–24).
Next major: QuickBooks Online connector (Sage 50 sunset — see Session 21 in SESSION_LOG.md).

---

## FABLE 5 AUDIT MILESTONE TRACKER

| ID   | Description                                          | Status       | File(s)                              |
|------|------------------------------------------------------|--------------|--------------------------------------|
| M0.1 | Approval-queue `job_config=` kwarg fix               | ✅ DONE       | `core/approval_queue.py`             |
| M0.2 | `ApprovalStatus.ARCHIVED` enum value added           | ✅ DONE       | `models/approval.py`                 |
| M0.3 | `bridge_sign_smoke.py` (30/30)                       | ✅ DONE       | `tests/bridge_sign_smoke.py`         |
| M1.1 | Live verify C1 fix                                   | ✅ DONE       | `scripts/archive/_m1_1_verify_*.py`  |
| M1.2 | Bridge sign decode (`decode_dr_cr`)                  | ✅ DONE       | `sage50/bridge_reader.py`            |
| M1.3 | Registry hardening (`_RegistryRow` Pydantic)         | ✅ DONE       | `core/client_registry.py`            |
| M1.4 | Double-post prevention (fan-out + within-batch dedup)| ✅ DONE       | `scripts/posting_agent.py`           |
| M1.5 | Archive/restore state machine                        | ✅ DONE       | `dashboard/app.py`                   |
| M2.1 | `JournalEntryAgent` → `ledger/` connector            | ✅ DONE (S22) | `agents/journal_entry.py`            |
| M2.2 | Bridge: returncode check + password via env var      | ✅ DONE       | `sage50/bridge_reader.py`, `sage50_bridge/Program.cs` |
| M2.3 | Split PROJECT_STATUS.md                              | ✅ DONE       | `PROJECT_STATUS.md`, `docs/SESSION_LOG.md` |
| M2.4 | Disable dead ops endpoints on Cloud Run              | ✅ DONE       | `dashboard/app.py`                   |

**Note:** `sage50_bridge/Sage50Bridge.exe` must be rebuilt after M2.2 (`sage50_bridge/build.ps1`).

---

## NEXT STEPS (ordered)

**Immediate — operational (pre-posting):**
1. ⚠ Add `sai_folder` column to `R:\bookkeeping\client_accounts.csv` (LIVE registry) —
   theotherapy rows → `Canadian Federation of theotherapy`
2. Run `scripts/_fix_gl_bank.py --dry-run` (expect 395+395) then `--commit` (Sage CLOSED)
3. Run `scripts/setup_alerts.ps1`
4. Start `scripts/posting_agent.py --watch`; first dashboard-driven post
5. Sage 50: Start New Year → 2026.SAI for theotherapy (unblocks 23 Jan-2026 entries)

**Fable 5 audit: all M0–M2 milestones complete.**

**QBO track:**
7. Register Intuit developer app (portal account created ✓; approval needed)
8. `ledger/qbo.py` sandbox end-to-end test once sandbox credentials issued

**Accounting tasks:**
- Concetta 2026-04 year-end: adjusting entries in `concetta_yearend_2026-04.xlsx`
- ⚠ Duplicate Dec 2025 journal entries J329–J348 (not yet addressed)
- ⚠ Trial balance as at 2025-04-30 (verify opening balance $19,077.69)

---

## KEY QUICK REFERENCE

**GCP:** `vtx-accounting-os-prod` | region `northamerica-northeast2`
**ADC:** jquinonez2980@gmail.com (run `gcloud auth application-default login` to refresh)
**API:** https://acumenai-api-lscziarcxa-pd.a.run.app (Cloud Run; JWT-gated `/api/live/*`)

**BQ datasets:**
- `vtx_audit.audit_log` — day-partitioned, clustered agent_id/event_type
- `vtx_accounting.*` — bank_transactions_raw/categorized, approval_queue, gl_transactions, ...
- `vtx_rag.document_chunks` — embeddings

**GCS:** `vtx-accounting-os-prod-vtx-exports`
  sage50/{raw|staging|archive|failed}/YYYY/MM/DD/{report_type}/

**Secrets (set via `gcloud secrets versions add <name> --data-file=-`):**
- `vtx-sage50-company-path` — path to .SAI file
- `vtx-sage50-password` — Sage 50 password (also readable as `VTX_BRIDGE_PASSWORD` env var)
- `vtx-gmail-oauth-credentials` — authorized_user JSON (v3, refreshed 2026-06-01)
- `acumen-dashboard-key` — dashboard API key (rotate: burned in old revisions)

**Live clients:**
- Concetta Enterprises Inc. — TD xxxx5443, GL 1060, YE Apr 30
- theotherapy — TD xxxx4733 (GL 1060, currently 1065) + BMO xxxx1555 (GL 1065), YE Dec 31

**Tests:** `Get-ChildItem tests\*smoke*.py, tests\p1_7_e2e.py | ForEach-Object { python $_.FullName }`
