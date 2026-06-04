# PROJECT_STATUS.md — Vertex AI Accounting OS
# Updated: 2026-06-04  |  Session: 17  (Claude Code harness hardening — Phases 0–4 + skills/cleanup)
# Trace: vtx-os-proj-001

## CURRENT PHASE
Phase 2 — Multi-Agent ADK Architecture (IN PROGRESS)

## COMPLETED STEPS

### Phase 0 — Foundation [COMPLETE 2026-05-07]
- [x] P0.1  GCP project vtx-accounting-os-prod created
- [x] P0.2  gcloud SDK 567.0.0 installed + authenticated as jquinonez2980@gmail.com
- [x] P0.3  All 10 APIs enabled
- [x] P0.4  Python 3.14.4 installed
- [x] P0.5  Python venv created at C:\vtx-os\.venv
- [x] P0.6  All requirements.txt packages installed (48 packages)
- [x] P0.7  BigQuery dataset vtx_audit created (northamerica-northeast2)
- [x] P0.8  BigQuery table vtx_audit.audit_log created (day-partitioned on event_ts, clustered on agent_id + event_type)

### Phase 1 — Agent Scaffolding [COMPLETE 2026-05-07]
- [x] P1.1  GCS bucket vtx-accounting-os-prod-vtx-exports created
             Location:    northamerica-northeast2
             Access:      Uniform bucket-level, public access blocked
             Versioning:  enabled
             Lifecycle:   raw/ → delete after 90d | archive/ → delete after 365d | noncurrent → delete after 30d
             Structure:   sage50/raw/ | sage50/staging/ | sage50/archive/ | sage50/failed/
             Module:      C:\vtx-os\sage50\csv_uploader.py (ReportType enum + upload/stage/archive/fail helpers)
- [x] P1.2  Pydantic base models — C:\vtx-os\models\
             base.py:    Severity, EventType (20 types), EventStatus, AgentEvent, AuditRecord
                         AuditRecord.ok() / .fail() factory helpers + to_bq_row() serializer
             sage50.py:  GLTransaction, ARInvoice, APBill, ChartOfAccountsEntry, Customer,
                         Vendor, TaxSummary (GST/HST/QST), PayrollEntry (CPP/EI),
                         InventoryItem, BankReconciliation
                         All models: Decimal amounts, date parsing, _S50Base.from_csv() + iter_csv_file()
- [x] P1.3  Secret Manager secrets created (northamerica-northeast2, user-managed replication)
             vtx-sage50-odbc-conn       v1 placeholder — set with: gcloud secrets versions add vtx-sage50-odbc-conn --data-file=-
             vtx-sage50-company-path    v1 placeholder — path to .sai company file
             vtx-cantax-api-key         v1 placeholder — Phase 2
             vtx-gmail-oauth-credentials v1 placeholder — Phase 2
             Client:  C:\vtx-os\core\secrets.py — get() | set_version() | typed accessors
                      In-process cache (thread-safe) + env-var override for local dev
             ⚠ ADC needed for SM path: run  gcloud auth application-default login
- [x] P1.4  OrchestratorAgent skeleton — C:\vtx-os\agents\
             core/audit.py          BQ streaming writer + stderr fallback (never drops events)
             agents/base.py         TaskType (12 types) | TaskRequest | TaskResult | AgentBase
                                    AgentBase.run() = handle() + timing + BQ audit (auto)
             agents/sage50_ingest.py Sage50IngestAgent: INGEST_SAGE50_CSV → GCS upload + row count
             agents/orchestrator.py OrchestratorAgent: TASK_CREATED → TASK_DELEGATED → TASK_COMPLETE
                                    Class-level registry; unknown task types fail cleanly
             Audit trail per task:  AGENT_START → TASK_CREATED → TASK_DELEGATED →
                                    AGENT_START(sub) → AGENT_COMPLETE(sub) →
                                    TASK_COMPLETE → AGENT_COMPLETE

## GCP PROJECT
Project ID:  vtx-accounting-os-prod
Region:      northamerica-northeast2  (Montreal)
Billing:     Free Trial ($410 credits applied)

## APIs ENABLED
Vertex AI:      [x]   BigQuery:          [x]
Cloud Storage:  [x]   Eventarc:          [x]
Gmail API:      [x]   Document AI:       [x]
Secret Manager: [x]   Cloud Resource Mgr:[x]
IAM:            [x]   Cloud Build:       [x]

## LOCAL ENVIRONMENT
gcloud CLI:     [x]  567.0.0
Authenticated:  [x]  jquinonez2980@gmail.com
Python:         [x]  3.14.4
venv created:   [x]  C:\vtx-os\.venv
deps installed: [x]  48 packages

## KEY PACKAGE VERSIONS (installed 2026-05-07)
google-cloud-aiplatform   1.150.0
google-cloud-bigquery     3.41.0
google-cloud-storage      3.10.1
google-cloud-documentai   3.14.0
google-cloud-secret-manager 2.28.0
google-genai              1.75.0
google-auth               2.51.0
pydantic                  2.13.4
httpx                     0.28.1
python-dotenv             1.2.2

## BIGQUERY RESOURCES
Dataset:  vtx-accounting-os-prod.vtx_audit
  Table:  audit_log
    Partition:  DAY on event_ts
    Cluster:    agent_id, event_type
    Schema:     event_id, event_ts, agent_id, agent_version, event_type,
                severity, session_id, user_email, resource_type, resource_id,
                action, status, duration_ms, input_tokens, output_tokens,
                error_code, error_message, metadata (JSON)

## GCS RESOURCES
Bucket: vtx-accounting-os-prod-vtx-exports
  sage50/raw/YYYY/MM/DD/{report_type}/     ← Sage 50 CSV drops land here
  sage50/staging/YYYY/MM/DD/{report_type}/ ← queued for BQ ingest
  sage50/archive/YYYY/MM/DD/{report_type}/ ← post-ingest, kept 365d
  sage50/failed/YYYY/MM/DD/{report_type}/  ← failed ingest + .error.txt sidecar

## SAGE 50 INTEGRATION
Method: CSV export + ODBC (no REST API)
Module: C:\vtx-os\sage50\csv_uploader.py
Report types: gl_transactions, ar_invoices, ap_bills, chart_of_accounts,
              customers, vendors, tax_summary, payroll, inventory, bank_reconciliation

## MODEL LAYER  (C:\vtx-os\models\)
base.py   →  Severity | EventType | EventStatus | AgentEvent | AuditRecord
sage50.py →  GLTransaction | ARInvoice | APBill | ChartOfAccountsEntry | Customer
              Vendor | TaxSummary | PayrollEntry | InventoryItem | BankReconciliation

## SECRET MANAGER  (northamerica-northeast2)
vtx-sage50-odbc-conn          v1  PLACEHOLDER  ← set your Sage 50 ODBC string here
vtx-sage50-company-path       v1  PLACEHOLDER  ← set path to .sai file here
vtx-cantax-api-key            v1  PLACEHOLDER  ← Phase 2
vtx-gmail-oauth-credentials   v1  PLACEHOLDER  ← Phase 2

## AGENT LAYER  (C:\vtx-os\agents\)
orchestrator.py  →  OrchestratorAgent (dispatcher + full audit trail)
sage50_ingest.py →  Sage50IngestAgent  [INGEST_SAGE50_CSV]
base.py          →  AgentBase | TaskType | TaskRequest | TaskResult
core/audit.py    →  BQ streaming writer (falls back to stderr, never silent)

## PHASE 1 STATUS  [COMPLETE 2026-05-07]
[x] P1.1  GCS bucket + folder structure (sage50/raw|staging|archive|failed)
[x] P1.2  Pydantic models (AuditRecord, 10 Sage 50 row types)
[x] P1.3  Secret Manager (4 secrets, placeholder versions)
[x] P1.4  OrchestratorAgent skeleton — dispatch + 7-event audit trail per task
[x] P1.5  BookkeepingAgent v1 — bank statement parser + categorization
             models/banking.py      BankTransaction | CategorizedTransaction | BookkeepingSummary
             sage50/bank_parser.py  Auto-detects RBC/TD/BMO/CIBC/Scotiabank/National/Desjardins/Generic
                                    Signed amounts, date normalisation, sha256 txn_id dedup
             sage50/categorizer.py  29 regex rules, Canadian context (CRA remittances, CPP/EI,
                                    Hydro/gas utilities, payroll processors, bank charges, etc.)
                                    Confidence scoring; threshold 0.80 → auto-approve vs needs_review
             agents/bookkeeping.py  BookkeepingAgent [BOOKKEEPING_RUN]
                                    GCS URI or local path, BQ stream to bank_transactions_raw +
                                    bank_transactions_categorized
             Smoke test (Dec 2025): 12 txns, 9 auto-categorized, 3 flagged for review
                                    Net movement CAD $10,734.80
- [x] P1.6  Approval queue + Google Chat notifications
             models/approval.py       ApprovalItem (BQ-backed) | ApprovalStatus (PENDING/APPROVED/REJECTED/ESCALATED)
                                      ApprovalItem.from_categorized() factory
             core/approval_queue.py   submit() | get_pending() | get_by_period()
                                      approve(queue_id, reviewer, gl_no) | reject() | escalate() via BQ DML UPDATE
             core/chat_notifier.py    Cards v2 webhook — header + summary + per-txn decorated rows + BQ button
                                      Graceful degradation: no crash if webhook unset or POST fails
             vtx-google-chat-webhook  Secret Manager secret created (v1 placeholder)
             BookkeepingAgent updated Steps 6+7: auto-queue needs_review + notify Chat (both opt-out via payload)
             Bug fixed:               Decimal in TaskResult.output now JSON-safe (model_dump mode='json')
                                      AuditRecord.to_bq_row metadata uses _SafeEncoder (Decimal/date)
             To configure Chat:       gcloud secrets versions add vtx-google-chat-webhook --data-file=-
                                      (paste webhook URL from Chat Space > Manage webhooks)
- [x] P1.7  End-to-end test with real December data [COMPLETE 2026-05-07]
             Test client:  Northview Consulting Inc. | Dec 2025 | RBC xxxx1234
             CSV:          data/test-client/dec-2025-bank.csv (TD-format, 20 transactions)
             Test script:  tests/p1_7_e2e.py — 62/62 checks passed
             Results:      20 txns parsed | 12 auto-categorized | 8 needs_review
                           7 BQ audit events | 8 approval_queue items submitted
                           Chat card captured (8 items, Cards v2 structure verified)
                           Approval flow: PENDING→APPROVED | PENDING→REJECTED | PENDING→ESCALATED
             BQ preview:   data/test-client/bq_raw_transactions.json
                           data/test-client/bq_categorized_transactions.json
                           data/test-client/bq_approval_queue.json
                           data/test-client/bq_audit_trail.json
                           data/test-client/chat_card.json
             Lessons & findings:
               1. CSV columns: TD format requires POSITIVE withdrawal amounts; minus signs
                  in the Withdrawals column flip the sign (deposits - withdrawals).
               2. Mock branch ordering: UPDATE DML contains "PENDING" in its WHERE clause;
                  always check "UPDATE" first before checking for "PENDING" keyword.
               3. Pipeline runs in ~50ms offline (mock BQ); logic is fully verified.
               4. session_id is shared across orchestrator + sub-agent (1 shared session).
               5. TD format auto-detected from CSV headers; no explicit bank_code needed.
               6. Production requires: gcloud auth application-default login (ADC not yet set).

## PHASE 1 STATUS  [COMPLETE 2026-05-07]
All P1.1–P1.7 steps complete. Phase 2 is next.

## LIVE PDF PIPELINE RUN  [COMPLETE 2026-05-07]
Client:  Concetta Enterprises Inc. | Dec 2025 | TD Bank xxxx5443
Source:  data/test-client/dec-2025-bank.pdf  (OCR'd TD Bank statement)
         Extracted: data/test-client/dec-2025-bank-extracted.csv
         Test script: tests/concetta_live_pipeline.py — 6/6 spot-checks passed
Results: 20 txns parsed (SERVICE CHARGE OCR unrecoverable — acceptable)
         0 auto-categorized (cheque/PC Mastercard types not in default ruleset — expected)
         20 needs_review — all queued to BQ approval_queue
         Total deposits: $23,249.07 | Total withdrawals: $9,819.46 | Net: +$13,429.61
         BQ tables (REAL — ADC live):
           vtx-accounting-os-prod.vtx_accounting.bank_transactions_raw
           vtx-accounting-os-prod.vtx_accounting.bank_transactions_categorized
         Audit trail: vtx-accounting-os-prod.vtx_audit.audit_log
         Chat: webhook not yet configured (graceful skip)
Fixes applied:
  - sage50/pdf_extractor.py: \b? on zero-width assertion removed (Python 3.12+)
  - sage50/pdf_extractor.py: date format changed to %Y-%m-%d (was DD/MM/YYYY)
  - sage50/bank_parser.py:   abs() applied to withdrawal/debit columns (all 7 parsers + generic)
Key OCR recoveries:
  - SENTRIX FINANCI INV: 23,249.07 credit — balance-chain corrected (OCR: "23., 249. 07")
  - PC MASTRCRD Z7W8Y6:  1,008.59 — balance-chain residual (OCR amount was garbled)
  - PC MASTRCRD H3H6L4:  90.05    — balance-chain residual (OCR: "9·0 :05")
ADC note: ADC now configured (jquinonez2980@gmail.com) — all BQ writes are live.

## PHASE 2 STATUS  [IN PROGRESS]
### Phase 2 — Multi-Agent ADK Architecture

- [x] P2.1  Orchestrator + Supervisor agent + ADK runtime [COMPLETE 2026-05-08]
             google-adk 1.33.0 installed
             agents/supervisor.py   — SupervisorAgent (LlmAgent, gemini-2.5-flash, Vertex AI)
                                      One tool: dispatch_task(task_type, payload_json)
                                      Converts natural language requests → TaskRequest dispatches
             agents/adk_runner.py   — Runner + InMemorySessionService
                                      run_sync(user_message, session_id) synchronous entry point
                                      Loads config/project.env (GOOGLE_GENAI_USE_VERTEXAI etc.)
             config/project.env     — GOOGLE_GENAI_USE_VERTEXAI=TRUE, GOOGLE_CLOUD_LOCATION=northamerica-northeast1
             tests/p2_1_adk_smoke.py — Live smoke test (Vertex AI + mock BQ) — 9/9 checks passed
             Gemini correctly dispatched RECONCILE_GL from natural language, ReconcileGLAgent
             ran end-to-end: MATCHED=19, UNMATCHED_BANK=1, UNMATCHED_GL=2
- [x] P2.2  A2A protocol wiring [COMPLETE 2026-05-09]
             agents/a2a.py        — A2A protocol types (A2ATask, A2AMessage, A2APart, A2ATaskStatus,
                                    AgentCard, AgentSkill) + A2AAgentServer (wraps AgentBase) +
                                    A2ATransport (in-process registry; HTTP-ready)
             agents/orchestrator.py — register() now auto-registers in A2ATransport;
                                      handle() routes through A2ATransport.send_task() instead of
                                      direct sub_agent.run() calls; _a2a_to_task_result() unpacks response
             tests/p2_2_a2a_smoke.py — 20/20 checks passed (offline, mock BQ)
                                        Registration, AgentCards, direct A2A dispatch, orchestrator dispatch,
                                        session_id propagation, error path, BQ audit + recon rows
- [x] P2.3  Gmail Comms agent [COMPLETE 2026-05-09]
             agents/gmail_comms.py  — GmailCommsAgent [SEND_CLIENT_EMAIL]
                                      Payload: to, subject, body (+ optional cc, html_body)
                                      Credentials from Secret Manager vtx-gmail-oauth-credentials
                                      (format: authorized_user JSON with client_id/secret/refresh_token)
                                      Graceful degradation: missing creds -> FAILURE, not crash
                                      Registered in OrchestratorAgent + A2ATransport
             agents/supervisor.py   — SEND_CLIENT_EMAIL added to Gemini instruction
             tests/p2_3_gmail_smoke.py — 18/18 checks passed (offline, mock Gmail API)
                                         Registration, AgentCard, MIME headers, multi-recipient,
                                         missing-creds failure path, orchestrator A2A dispatch, audit trail
             To configure: store authorized_user JSON in Secret Manager:
               python scripts/gmail_auth.py   (interactive OAuth flow — see scripts/)
               OR: gcloud secrets versions add vtx-gmail-oauth-credentials --data-file=-
- [x] P2.4  Eventarc trigger (GCS object.finalize -> orchestrator) [COMPLETE 2026-05-09]
             functions/gcs_ingest_trigger.py  -- CloudEvent handler + _route() routing logic
             main.py                          -- Cloud Functions Gen 2 entry point (1-line import)
             functions/__init__.py            -- package marker
             agents/sage50_ingest.py          -- extended: gcs_uri payload key (file already in GCS)
                                                 _count_rows_from_gcs() + _copy_raw_to_staging()
             scripts/deploy_p2_4.ps1         -- SA creation, IAM, function deploy, Eventarc trigger
             requirements.txt                -- added functions-framework>=3.5.0
             tests/p2_4_eventarc_smoke.py    -- 27/27 checks passed (offline, mock GCS + BQ)
             Routing rules:
               sage50/raw/YYYY/MM/DD/{report_type}/*.csv  ->  INGEST_SAGE50_CSV
               odbc-triggers/{report_type}.trigger        ->  INGEST_SAGE50_ODBC
               bank-statements/**/*.csv                   ->  BOOKKEEPING_RUN
               sage50/{staging,archive,failed}/...        ->  ignored (internal moves)
             Failure: RuntimeError -> Cloud Run 5xx -> Eventarc retry
             To deploy: run scripts/deploy_p2_4.ps1 from project root
- [x] P2.5  RAG agent [COMPLETE 2026-05-09]
             models/rag.py          -- DocumentChunk (list[float] embedding), DocumentType, RagChunkResult
             agents/rag.py          -- RagAgent [INDEX_DOCUMENT + RAG_QUERY] same agent_id "rag-agent"
                                       _chunk_text() sliding window (size 1000, overlap 100)
                                       _embed_texts() via Vertex AI text-embedding-005 (batched 250)
                                       _vector_search() via BQ VECTOR_SEARCH with filter support
             agents/base.py         -- INDEX_DOCUMENT, RAG_QUERY added to TaskType
             agents/orchestrator.py -- RagAgent registered for both TaskTypes
             agents/supervisor.py   -- INDEX_DOCUMENT + RAG_QUERY added to Gemini instructions
             core/bq_loader.py      -- schema_from_model handles list[T] -> T REPEATED
                                       ensure_dataset() added (creates vtx_rag dataset on demand)
             tests/p2_5_rag_smoke.py-- 24/24 checks passed (offline, mock Vertex AI + mock BQ)
             Storage:
               vtx_rag.document_chunks  (FLOAT64 REPEATED embedding; clustered client_id/document_type)
             Supported document types: engagement_letter, t2_return, hst_return, gl_summary,
                                       bank_recon, chart_of_accounts, generic
- [x] P2.6  Engagement letter + monthly close demo [COMPLETE 2026-05-11]
             demo/monthly_close_demo.py   -- run_pipeline() orchestrates 6 agents via A2A with shared session_id
                                             _compose_email() builds formatted close email from all step outputs
                                             CLI entry point uses Concetta Enterprises test data
             demo/__init__.py             -- package marker
             agents/orchestrator.py       -- PrepareHSTReturnAgent registered (was orphaned from early work)
             tests/p2_6_monthly_close_smoke.py -- 27/27 checks passed (offline, mock BQ + Vertex AI + Gmail)
             Pipeline steps:
               1  INDEX_DOCUMENT       RagAgent indexes engagement letter (chunk_size=800, overlap=80)
               2  BOOKKEEPING_RUN      BookkeepingAgent parses bank statement CSV
               3  RECONCILE_GL         ReconcileGLAgent matches bank vs Sage 50 GL (bank_csv_path bypasses BQ)
               4  PREPARE_HST_RETURN   PrepareHSTReturnAgent computes GST34 lines from Tax Summary CSV
               5  RAG_QUERY            RagAgent retrieves engagement letter context for email footer
               6  SEND_CLIENT_EMAIL    GmailCommsAgent drafts and sends monthly close email
             Email body includes: bank activity, GL recon status, HST filing amounts, RAG context snippet
             Live run: python demo/monthly_close_demo.py (requires ADC + real test-client CSV files)
- [x] P2.7  Full monthly close — one real client [COMPLETE 2026-05-11]
             Live GCP run: Concetta Enterprises Inc. | Dec 2025 | TD Bank xxxx5443
             tests/p2_7_live.py  -- 25/25 checks passed (real BQ + Vertex AI + dry-run email)
             scripts/gmail_auth.py -- OAuth2 helper to configure live Gmail send (run once, interactive)
             demo/monthly_close_demo.py -- fixed: now uses dec-2025-bank-extracted.csv (Concetta PDF-extracted CSV)
                                           added dry_run_email + email_output_path params
             Pipeline results (live BQ):
               Bookkeeping:   20 txns | deposits $23,249.07 | withdrawals $9,819.46 | net +$13,429.61
                              all 20 flagged needs_review (cheques + PC Mastercard — no default rule match)
               GL Recon:      19/20 matched | 1 unmatched bank | 2 unmatched GL | DIFFERENCES NOTED
               HST Return:    Line 101 $45,000.00 | Line 103 $5,850.00 | Line 106 $261.21
                              Line 109 $5,588.79 net tax owing | filing due 2026-01-31
               RAG:           engagement letter indexed (1 chunk) + retrieved successfully
               Email:         generated + saved to data/test-client/close-email-2025-12.txt
                              (dry-run; configure Gmail: python scripts/gmail_auth.py)
             Human approval gate:
               20 transactions queued in vtx_accounting.approval_queue (all needs_review)
               Review at: https://console.cloud.google.com/bigquery?project=vtx-accounting-os-prod
               After review: re-run demo to send close email via live Gmail
             BQ tables written (live): vtx_accounting.bank_transactions_raw + categorized + gl_reconciliation
                                        + hst_returns | vtx_rag.document_chunks | vtx_audit.audit_log
             Note: Vertex AI TextEmbeddingModel.from_pretrained deprecation warning (removal June 2026);
                   migrate to google-genai SDK before that date

## ACCOUNTING AGENTS  [early work — not yet sequenced into phases]
The following were built ahead of schedule. They will be properly sequenced into a later
phase once the Phase 2 ADK runtime is in place and agents communicate via A2A.

- [x] ODBC reader + BQ loader [built session 5]
             sage50/odbc_reader.py   — discover_tables() + fetch_* for all 10 report types
             core/bq_loader.py       — schema_from_model(), ensure_table(), load_rows()
             agents/sage50_odbc.py   — Sage50OdbcAgent [INGEST_SAGE50_ODBC]
             BQ dataset vtx_accounting created (northamerica-northeast2)
- [x] GL reconciliation agent [built session 5]
             models/reconciliation.py   GLEntry | ReconciliationItem | ReconciliationSummary
             sage50/gl_parser.py        Sage 50 GL CSV parser (MM/DD/YYYY, bank account filter)
             agents/reconcile_gl.py     ReconcileGLAgent [RECONCILE_GL]
                                        Greedy best-first matching: amount + date + ref scoring
             data/test-client/concetta-dec2025-gl.csv  21 GL entries, Dec 2025
             tests/p2_2_reconcile_gl.py  27/27 checks passed
             Results: MATCHED=19, UNMATCHED_BANK=1, UNMATCHED_GL=2, net_diff=+$4,746.09
- [x] HST/GST return agent [COMPLETE — integrated in P2.6]
             models/hst_return.py         HSTReturnLine | HSTReturnSummary
             agents/prepare_hst_return.py PrepareHSTReturnAgent [PREPARE_HST_RETURN]
             tests/p2_3_hst_return.py     19/19 checks passed (Concetta Dec 2025: line_109=$5,588.79, due 2026-01-31)
             Registered in OrchestratorAgent as of P2.6

## BQ DATASETS
vtx_audit      — audit_log (day-partitioned event_ts, clustered agent_id/event_type)
vtx_accounting — gl_transactions, ar_invoices, ap_bills, chart_of_accounts, customers,
                  vendors, inventory, payroll, tax_summary, bank_reconciliation
                  (tables created lazily; all include _loaded_at + _session_id tracking cols)

## AI COLLABORATION INFRASTRUCTURE  [COMPLETE 2026-05-08]
- [x] CLAUDE.md          — persistent project brain (architecture, conventions, domain knowledge,
                            OCR artifacts, sign rules, testing patterns, session workflow)
- [x] .claudeignore      — excludes .venv, data/, bytecode, credentials from auto-context
Both files should be read at the start of every session alongside PROJECT_STATUS.md.

## CONCETTA CATEGORIZATION RULESET  [COMPLETE 2026-05-11]
- [x] sage50/categorization_rules.py  — ConcettaRuleset (9 rules) wired into BookkeepingAgent
           Fixes applied to ConcettaRuleset:
             - Removed incorrect `from models.categorization import CategorizedTransaction` import
             - Fixed typo: "MONTHY PLAN FEE" → "MONTHLY PLAN FEE" in _rule_bank_fees
             - Fixed exact-match → substring match in _rule_card_clearing
               ("PC MASTRCRD" and "TD VISA" now matched with `in` operator, not `==`)
           agents/bookkeeping.py  — client_id="concetta" payload key triggers ConcettaRuleset
             _categorize_concetta() bridge: converts (gl_no: int, gl_name, confidence_pct: Decimal)
             to CategorizedTransaction; divides confidence by 100 for 0.0–1.0 scale
           tests/concetta_categorization_smoke.py  — 6/6 checks passed (offline, mock BQ)
           Before/After (Concetta Enterprises Dec 2025, 20 transactions):
             Before (default rules):  0/20 auto-categorized | 20/20 needs_review
             After  (ConcettaRuleset): 12/20 auto-categorized |  8/20 needs_review
           Auto-categorized breakdown:
             10x PC MASTRCRD* → GL 5750 Mastercard       (98% confidence)
              2x MONTHLY/PAPER STMT FEE → GL 5200 Bank Charges (90% confidence)
           Still needs review (8):
             5x CHQ# cheques, 1x CASH WITHDRAWAL, 1x AMEX CARDS, 1x SENTRIX income
           Activation: add client_id="concetta" to BOOKKEEPING_RUN payload
- [x] LIVE RUN with ConcettaRuleset [COMPLETE 2026-05-11]
             tests/concetta_live_pipeline.py updated: client_id="concetta" + revised spot-checks
             7/7 spot-checks passed (real BQ — ADC live)
             Results: 20 txns | auto_categorized=12 | needs_review=8
                      deposits $23,249.07 | withdrawals $9,819.46 | net +$13,429.61
             BQ writes: vtx_accounting.bank_transactions_raw + bank_transactions_categorized (live)
             Approval queue: 8 items submitted to vtx_accounting.approval_queue
             Chat: webhook not configured — graceful skip (configure via Secret Manager)

## INTEGRATIONS  [2026-05-12]
- [x] Gmail OAuth — COMPLETE
             scripts/gmail_auth.py updated: gmail.send + gmail.readonly scopes
             prompt="consent" added to force full scope screen on re-auth
             scripts/verify_gmail_oauth.py — 6/6 checks passed
               Secret readable | JSON valid | Fields present | Token refreshed
               Gmail API responds (jquinonez2980@gmail.com) | Inbox readable (~201 messages)
             Stored in Secret Manager: vtx-gmail-oauth-credentials (latest version)
             To re-auth: python scripts/gmail_auth.py --client-secret config\gmail_oauth_client.json
- [ ] Google Chat webhook — DEFERRED
             Requires Google Workspace account (not available — using email notifications only)
             vtx-google-chat-webhook secret exists as placeholder; chat_notifier degrades gracefully
             When available: gcloud secrets versions add vtx-google-chat-webhook --data-file=-
- [ ] Sage 50 ODBC — PENDING
             vtx-sage50-odbc-conn secret exists as placeholder
             Set via: echo "DSN=<name>;UID=sysadmin;PWD=<pwd>" | gcloud secrets versions add vtx-sage50-odbc-conn --data-file=-

## CONCETTA 2026.SAI DATA CLEANUP  [2026-05-21 / 2026-05-22]

### Sage50Bridge updates
- [x] `multiUser=true` in `OpenDatabase()` call (was false) — allows connection while Sage 50 UI is open
       Still requires company closed (File → Close Company) before any write operation
- [x] SDK 500-row cap documented: `RunSelectQuery()` returns at most ~500 rows; date filters
       on 2026 dates return 0 rows (fiscal-date mapping), triggering all-time fallback still capped at 500.
       Workaround: use Sage 50 UI → Reports → General Journal → Export CSV; parse with `scripts/purge_from_csv.py`

### January 2026 duplicate purge  [COMPLETE 2026-05-21]
- Root cause: monthly close pipeline posted BNK entries 2–3× before idempotency guard was added
- Fix: `agents/journal_entry.py` — entry-level idempotency added (key = date + description[:39] + amount);
       `skipped_duplicates` count in TaskResult output
- `scripts/purge_from_csv.py` — permanent CSV-based purge tool (parse Sage 50 GL export →
       detect duplicates by (date, description[:39], debit_acct, amount) → post RVRSL reversals)
- 36 reversing entries posted (J159–J194): source=RVRSL, comment="VOID DUP Jxxx"
- Smoke test: `tests/journal_entry_smoke.py` — 8 test groups incl. idempotency (Tests 7–8)

### Trial balance corrections — Concetta 2026.SAI  [COMPLETE 2026-05-22]
Corrected two balance sheet errors caused by duplicate rollover from prior fiscal year:

| Entry | Date       | Debit                          | Credit                         | Amount          | Purpose                        |
|-------|------------|-------------------------------|-------------------------------|-----------------|--------------------------------|
| J195  | 2025-05-01 | Bank 1100 (lId 11000000)      | Retained Earnings 3500 (35000000) | $2,245,888.40 | Opening balance correction     |
| J196  | 2025-05-31 | Bank 1100 (lId 11000000)      | Retained Earnings 3500 (35000000) | $6,874.82     | May 2025 closing balance adj   |

Post-correction trial balance (Concetta 2026.SAI):
- Bank 1100 at May 31, 2025: **$12,202.87** ✓
- Bank 1100 at May 1, 2025:  **$12,202.87** (Sage 50 trial balance is period-based/monthly — May 1 and May 31
  return same closing balance for the May period; run "as at Apr 30" to see the pre-May opening balance)

**Pending:** Run trial balance as at **2025-04-30** — should show $19,077.69 (opening balance carried forward).
If it doesn't, an additional correction entry may be needed dated April 30, 2025.

Retained Earnings 3500: lId = 35000000 (display code × 10000 pattern; not in CONCETTA_ACCOUNT_MAP).

## DOCUMENT AI BATCH OCR — LARGE PDF PARSING  [COMPLETE 2026-05-23]
Session 12: diagnosed and fixed 0-transaction failure for Concetta Feb 2026 statement (18 MB scanned TD Bank PDF).

### Root causes and fixes (commit 45df822)

**Fix 1 — `_MDAY_RE` regex (`sage50/bank_statement_ocr_parser.py`)**
- TD Bank OCR outputs "FEB02" (no space) not "FEB 02" — `\s+` between month and day failed to match
- Changed to `\s*` (optional space) + `\b` trailing word boundary

**Fix 2 — Bounding-box row reconstruction (`core/docai_ocr.py`)**
- Document AI batch mode reads wide multi-column tables column-by-column: descriptions appear on
  separate lines from amounts, dates, and balances. Standard text field unusable for row parsing.
- Added `_reconstruct_row_ordered_text()`: sorts all OCR line elements by (center_Y, center_X)
  using `normalizedVertices` bounding boxes, groups by Y proximity (ROW_TOL=0.004), concatenates
  with 2-space separator → "DESCRIPTION  AMOUNT  DATE  BALANCE" on one line per transaction
- ROW_TOL calibration: within-row Y variation ≤0.0017, between-row gap ≥0.009 on TD Bank scans
  (0.004 is mid-point; 0.012 first attempt was merging 3 adjacent rows)
- GCS upload retry deadline: `api_retry.Retry(deadline=660)` — google-api-core default 120s was
  too short for 18 MB file; `timeout=600` on upload_from_file sets per-request timeout separately

**Fix 3 — Mid-line date search + BALANCE FORWARD priority (`sage50/bank_statement_ocr_parser.py`)**
- Added `_MDAY_RE_MID` (no `^` anchor) and `_try_find_date_mid()` to find date tokens mid-line
  (TD Bank row-reconstruction layout: DESCRIPTION→AMOUNT→DATE→BALANCE, date not at line start)
- `_BAL_FORWARD_RE` check added BEFORE `_try_find_date_mid` in `_parse_lines()`:
  "BALANCE FORWARD JAN30 12,713.96" was being parsed as a JAN30 credit transaction because
  `_try_find_date_mid` found the embedded date first; this also left prev_balance=None for all
  subsequent transactions, causing debit/credit misclassification

**Fix 4 — Watcher error propagation (`scripts/gmail_watcher.py`)**
- `_process_pdf()` return value was discarded — zero-transaction errors didn't set `all_ok=False`
- Email was marked as read even on parse failure; fixed by capturing and checking the `"error"` key

### Results
- Input: `HWY 7 & PINEVALLEY.pdf` — 18,270,858 bytes, Concetta Enterprises Feb 2026
- Output: `R:\Concetta Enterprises Inc\drop\HWY_7___PINEVALLEY-2026-02.csv` — 33 transactions
- All transactions correctly classified as debits; running balances match statement
  (e.g., 11,820.94 after FEB02 CHQs → 5,343.64 final balance FEB27)
- BookkeepingAgent: 18 auto-categorized | 15 needs review
- One missing transaction: MONTHLY PLAN FEE $19.00 (page 2 layout edge case — will surface in GL recon)
- 97% recall (33/34) — acceptable for scanned PDF via batch OCR
- [FIXED in Session 15, 2026-06-01] The $19.00 fee was an OCR row-split (date wrapped
  to a bare next line). Now recovered → 34/34. See Session 15 changes below.

## SESSION 13 CHANGES  [2026-05-28]

### 1 — High-performance PDF extractor: `sage50/statement_extractor.py`

New module replacing the single DocAI-always path in `gmail_watcher.py` with a
three-tier confidence-routed pipeline:

| Path | Latency | Trigger |
|---|---|---|
| PyMuPDF (fitz) | 10–50 ms/page | Default — digital-native PDFs (online banking downloads) |
| pdfplumber | 200–800 ms/page | Fallback — complex digital layouts below confidence threshold |
| Document AI OCR | 10–90 s total | Last resort — scanned / image-only PDFs (e.g. 18 MB Concetta scan) |

Confidence score = printable-char density per page, normalised to [0, 1].
Threshold = 0.40 (configurable). Scanned PDFs score ~0.0 and cascade to DocAI
exactly as before; digital PDFs score ~0.9 and exit after PyMuPDF in ~50 ms.

**Public API:**
- `BankStatementExtractor.extract_transactions(pdf_path, bank="auto") -> list[BankTransaction]`
- `BankStatementExtractor.extract_to_csv(pdf_path, csv_path) -> Path`
- `BankStatementExtractor.to_dataframe(txns) -> pd.DataFrame`
- `extract_batch(pdf_paths, max_workers=4)` — thread-pool batch processing
- `benchmark(pdf_path)` — times all three paths, prints comparison table

Text from any path feeds into the existing `bank_statement_ocr_parser.parse_ocr_text()`;
conversion to `BankTransaction` applies the sign convention (credit − abs(debit)).

**Integration point (not yet wired — next step):**
Replace lines 173–192 of `scripts/gmail_watcher.py`:
```python
# Before (always DocAI):
ocr_text = ocr_pdf_bytes(pdf_path.read_bytes())
n = parse_and_write_csv(ocr_text, csv_path)

# After (PyMuPDF first, DocAI only for scanned):
from sage50.statement_extractor import extract_to_csv
extract_to_csv(pdf_path, csv_path)
```

**Dependencies added to requirements.txt:**
- `pymupdf>=1.23.0`
- `pdfplumber>=0.10.0`  (was used but not pinned)
- `pandas>=2.0.0`

### 2 — demo/monthly_close_demo.py cleanup

- `EventStatus.FAILURE` → `EventStatus.SKIPPED` for the skip-HST branch
- Removed redundant `from models.base import EventStatus` inside the `if` block
- `agent_id="hst-return-agent"` → `"prepare-hst-return-agent"` (matches class constant)
- Removed `skip_hst: bool` parameter — skip condition now derived from `tax_csv_path is None`
  (eliminates inconsistent state where `tax_csv_path=None, skip_hst=False` would crash)
- Hoisted `_dec(key, d)` to module level; `_print_summary` now uses it instead of inline `Decimal(str(...))`
- Reverted `→` → `->` cosmetic change (project convention is Unicode arrow)

## SESSION 14 CHANGES  [2026-05-29]
Phase 2 close-out: wired the high-performance extractor into the live pipeline and
hardened the extraction layer. Surgical changes only — no stable module rewrites.

### 1 — `statement_extractor` wired into `scripts/gmail_watcher.py`  [H1]
- `_process_pdf()` Step 1 now calls `BankStatementExtractor().extract(pdf_path)` instead of
  the always-DocAI `ocr_pdf_bytes()` path. Digital PDFs exit at PyMuPDF (~50 ms); only
  scanned PDFs reach Document AI. Logs the chosen path, confidence, pages, and elapsed ms.
- TaskResult now also reports `extract_path` + `extract_ms` for observability.
- Single extraction + parse is reused (no double DocAI call): `extract()` returns parsed
  transactions, then `write_csv()` serialises them.

### 2 — Public `extract()` seam + parse-aware cascade  [M3 + M1] (`sage50/statement_extractor.py`)
- Promoted internal `_extract_text` to public `extract(pdf_path, bank="auto") -> ExtractionResult`.
- `ExtractionResult` now carries `bank_code` + parsed `transactions` (+ `txn_count` property)
  so callers extract once and reuse — no second extraction or parse.
- Cascade is now PARSE-AWARE: a path wins only if `confidence >= threshold AND txn_count > 0`.
  Guards against dense-but-unparseable digital PDFs that previously exited early with 0 txns.
- `extract_to_csv` now logs a warning (instead of silently writing 0 rows) on empty text.

### 3 — Document AI sync-path row reconstruction  [H2] (`core/docai_ocr.py`)
- `_ocr_sync` (PDFs < 5 MB) now applies `_reconstruct_row_ordered_text` via
  `documentai.Document.to_json()`, matching the batch path. Previously small SCANNED PDFs
  returned column-disordered text and parsed 0 transactions. Falls back to raw text on error.

### 4 — Decimal preserved in `to_dataframe`  [M2]
- `BankStatementExtractor.to_dataframe` no longer coerces amount/balance to `float`
  (object dtype Decimal). Honours the "money never float" convention; view-only helper.

### 5 — Tests + docs  [M4]
- `tests/statement_extractor_smoke.py` — 8/8 offline checks (fake paths; no GCP):
  fixture parse, bank detect, parse-aware fallthrough, force_path, sign convention,
  extract_to_csv row count. (to_dataframe Decimal check skips if pandas absent.)
- CLAUDE.md directory map: `statement_extractor.py` marked CANONICAL; `pdf_extractor.py`
  marked LEGACY (TD-only, kept for balance-chain logic).

### Live validation (TODO — needs ADC + real PDFs)
  - `python scripts/gmail_watcher.py --client concetta --period 2026-02 --dry-run`
  - `python -c "from sage50.statement_extractor import benchmark; benchmark('<digital TD .pdf>')"`
    Expect ~50 ms PyMuPDF vs ~40 s DocAI on a digital statement.

## SESSION 15 CHANGES  [2026-06-01]
Multi-client routing for the Gmail bank-statement watcher, plus two bug fixes
surfaced while validating the Concetta Feb 2026 statement live. Gmail OAuth was
re-authed (token had expired) — credentials version 3 stored in Secret Manager.

### 1 — Multi-client routing (route incoming statements to the right client)
Until now the watcher was single-client: a required `--client` flag + a hardcoded
`_CLIENT_CONFIGS` dict (only `concetta`) applied to every inbox PDF. Now each
statement is routed by the **bank account number printed on the statement**,
matched against a maintained CSV registry on R:.

- **`sage50/bank_statement_ocr_parser.py`** — added `extract_account_no(text) -> str | None`.
  Reuses the legacy `\b(\d{4}-\d{7})\b` regex; `Counter.most_common` picks the most
  frequent match (robust against per-cheque OCR typos). Returns digits-only
  (e.g. `18905315443`) or None. TD-format today; extension point for other banks.
- **`core/client_registry.py`** (NEW) — replaces the hardcoded dict.
  `@dataclass ClientConfig(account_no, r_folder, client_id, gl_bank_account, bank,
  sender_email)` with `account_masked` property (`xxxx<last4>`).
  `load_registry()` reads `R:\bookkeeping\client_accounts.csv` (env override
  `VTX_CLIENT_REGISTRY`), keyed by normalized full account digits; validates
  required columns; raises FileNotFoundError (with create-instructions) if absent.
  `resolve(text, registry)` → ClientConfig | None.
  CSV columns: `account_no,r_folder,client_id,gl_bank_account,bank,sender_email`.
  Seeded with the Concetta row (account 1890-5315443 → Concetta Enterprises Inc).
- **`core/gmail_notifier.py`** — added `apply_label(msg_id, label_name)`: applies a
  label WITHOUT removing UNREAD (quarantine; reuses `_get_or_create_label`).
- **`core/chat_notifier.py`** — added `send_alert(title, lines)`: simple titled
  Cards v2 text alert; degrades gracefully if webhook unset/POST fails.
- **`scripts/gmail_watcher.py`** — deleted `_CLIENT_CONFIGS`/`_resolve_client`;
  loads the registry once at startup (fail fast if missing/empty). `--client` is now
  OPTIONAL (manual pin/override for testing). In `_process_pdf`, after extraction it
  resolves the client from the parsed account:
    - Auto mode: unique match → use it; no match/unreadable → unrouted sentinel.
    - Pinned mode (`--client`): mismatch guard — parsed account ≠ pinned → unrouted.
  Unrouted attachments → `apply_label(..., "vtx-unrouted")` + Chat alert, email
  left UNREAD for retry; `mark_read` only when ALL attachments book OK. Never
  mis-books one client into another's GL.
- **`tests/client_routing_smoke.py`** (NEW) — 10/10 offline checks (temp CSV +
  embedded TD fixture + cached real Jan OCR → 18905315443).

### 2 — Period detection fix (`scripts/gmail_watcher.py`)
The Feb statement was tagged `2026-04`: filename `HWY 7 & PINEVALLEY.pdf` has no
date, so it fell through to the email-arrival heuristic (statement arrived late).
- Added `_period_from_text()` — parses TD's `Statement From - To` range
  (e.g. `JAN 30/26 - FEB 27/26`) and returns the CLOSING (To) month/year.
  Authoritative.
- Added `_period_from_subject()` — parses `February 2026` from the email subject.
- Period computation moved to AFTER extraction (needs OCR text). New precedence:
  `override → statement text → filename → subject → email-date`.
- Result: Feb → `2026-02` (CSV `HWY_7___PINEVALLEY-2026-02.csv`).

### 3 — Feb $19 MONTHLY PLAN FEE OCR row-split fix (`sage50/bank_statement_ocr_parser.py`)
The 18 MB Feb scan wrapped one transaction's date onto its own line — the amount
stayed above, the date `FEB27` landed on a bare following line — so the fee was
dropped (the resolved "97% recall, 33/34" from Session 12).
- Added `_date_from_wrapped_line()`: returns a date only if the line is a bare
  date token carrying NO amount (`_AMOUNT_RE` requires 2 decimals, so day digits
  don't match). Real txn/balance lines always have an amount, so they're not stolen.
- `_parse_lines` switched to an indexed loop: when a line has a description+amount
  but no date AND the next line is a bare wrapped date, it adopts that date and
  consumes the line.
- Result: Feb now parses **34** transactions with the $19.00 debit on 2026-02-27.
- Regression check added to `tests/statement_extractor_smoke.py` (now 10/10).

### Live validation (this session)
- Gmail re-authed (creds v3). `python scripts/gmail_watcher.py --once --dry-run`
  (no `--client`): Feb statement auto-routed to Concetta Enterprises Inc (xxxx5443)
  from the parsed account, period `2026-02`, **34 transactions** parsed. Dry-run
  correctly skipped R:\, GCS, and BookkeepingAgent.
- No regressions: Jan still 37 txns; statement_extractor_smoke 10/10;
  client_routing_smoke 10/10.

### Notes / follow-ups
- Registry currently holds 1 client (Concetta). To onboard more, append rows to
  `R:\bookkeeping\client_accounts.csv` — no code change needed.
- `extract_account_no` and `_period_from_text` are TD-format; generalize per bank
  as non-TD clients onboard.
- New clients without a dedicated ruleset fall back to DEFAULT_RULES in
  BookkeepingAgent — per-client rules needed at scale.
- Session 15 changes committed (280207a) and pushed.

## SESSION 16 CHANGES  [2026-06-03]
Two major features: CHQ payee extraction from embedded cheque images, and the
year-end worksheet generator for April 30 fiscal year-ends. Concetta's 2026
year-end bookkeeping is complete; year-end worksheet generated and ready for
adjusting entries.

### 1 — CHQ payee extraction from cheque image pages  [commit 2899f75]
TD Bank statements embed scanned cheque images after the transaction ledger.
Previously CHQ entries only showed `CHQ#00788-1141529082` with no payee.

- **`models/banking.py`** — added `payee: str | None = None` to `BankTransaction`
- **`core/docai_ocr.py`** — refactored to support per-page text lists:
    - `_page_texts_from_doc()` — new: same bounding-box reconstruction per page
    - `_ocr_sync` / `_ocr_batch` now return `tuple[str, list[str]]`
    - `ocr_pdf_bytes_with_pages()` / `ocr_pdf_file_with_pages()` — new public functions
    - `_extract_pymupdf` page list fixed: `pages.append(t or "")` (not `if t:`) to preserve page index alignment
- **`sage50/cheque_extractor.py`** (NEW):
    - `ChequeInfo` — cheque_no, payee, amount, confidence (0.0–1.0)
    - `_is_cheque_page()` — true if "Pay to" present and no ledger keywords (BALANCE FORWARD, etc.)
    - `_parse_cheque_page()` — splits on "Pay to" occurrences for 2-cheque-per-page layout; MICR preferred over No. label for cheque number
    - `extract_cheque_map(page_texts)` → `{cheque_no: ChequeInfo}` across all pages
- **`sage50/statement_extractor.py`** — all three extract paths return 4-tuple (text, conf, pages, page_texts); `_enrich_cheque_payees()` mutates CHQ transaction descriptions in-place (`CHQ#00788` → `CHQ#00788 - Rogers Communications Inc.`); `payee` field populated from enriched description
- **`sage50/categorization_rules.py`** — added `_CHEQUE_PAYEES` list (empty until first live run confirms payee names) and `_rule_cheque_payee` as priority-1 rule in ConcettaRuleset
- **`scripts/gmail_watcher.py`** — added `_archive_pdf_to_gcs()`: uploads original PDF to `bank-statements/pdf/YYYY/MM/DD/{client_id}/` in GCS before temp cleanup
- **`tests/cheque_extractor_smoke.py`** (NEW) — 26/26 checks: page classification, two-cheque parse, single cheque, garbled MICR, extract_cheque_map, ledger page exclusion

### 2 — Year-end worksheet generator  [commits 21ca1dd, 9b9c168, aec9741]
Concetta Enterprises Inc. fiscal year end: April 30, 2026. All bookkeeping complete.

- **`core/client_registry.py`** — added `year_end_month: int = 0` (1–12; 0=unset) to `ClientConfig`; `load_registry()` reads optional column from CSV
- **`R:\bookkeeping\client_accounts.csv`** — added `year_end_month` column; Concetta set to `4`
- **`sage50/trial_balance_parser.py`** (NEW):
    - `TBLine` — account_no, description, debit, credit (Decimal)
    - `parse_trial_balance(csv_path)` — skips company-name preamble, handles header variants (`Account Number`, `Debits`, `Credits`), filters to posting accounts via `^\d{3,6}$`
    - `find_tb_csv(drop_dir, period)` — locates `tb-{period}.csv` etc. with Sage 50 export instructions in error
- **`core/year_end_worksheet.py`** (NEW):
    - `populate_worksheet(template_path, output_path, client_name, year_end_date, tb_lines, prepared_by)` — openpyxl populates the professional template
    - Writes Cover Sheet D8-D11 (D9 number_format overridden to General)
    - Writes Worksheet cols A-D; cols E-M formulas never touched
    - Deletes unused template formula rows (rows n+4 to 201) so TOTALS lands 2 rows after last account
    - Rewrites TOTALS/Diff/BalanceCheck formulas with corrected row references
    - Styling: alternating white/light-blue rows, navy header with white bold font, blue-grey TOTALS row, `#,##0.00` on all numeric columns, freeze top row, explicit column widths
- **`scripts/year_end.py`** (NEW) — CLI `--client`, `--period`, `--tb-csv`, `--dry-run`; validates year_end_month match; saves to `R:\{r_folder}\Year End\{client_id}_yearend_{period}.xlsx`
- **`tests/year_end_worksheet_smoke.py`** (NEW) — 20/20 checks: cover sheet values, worksheet data, row pruning, formula not overwritten, error handling

### Live generation — Concetta 2026-04
```
python scripts/year_end.py --client concetta --period 2026-04 \
  --tb-csv "R:\Concetta Enterprises Inc\Trial Balance 2026.csv"
```
- 47 posting accounts | TB Debit = TB Credit = $350,368.99 (perfectly balanced)
- Output: `R:\Concetta Enterprises Inc\Year End\concetta_yearend_2026-04.xlsx`
- Status: ready for adjusting entries in **2. Adjusting Entries** tab

## SESSION 17 CHANGES  [2026-06-04]
Claude Code harness hardening — a 5-phase setup audit/implementation. Not product
code; this makes the day-to-day bookkeeping work faster and safer to run with Claude.

### Phase 0–1 (prior commits this session) — hygiene + safety rails
- Consolidated 120 one-off permissions → 23 patterns; UTF-8 env in settings (e9cc761)
- Write guard, permission carve-outs, pytest harness (`test_smoke_suite.py`/`pyproject.toml`),
  CLAUDE.md rules (887ebbb). `guard-prod-writes.py` PreToolUse hook logs Sage/BQ-DML/secret/--commit.

### Phase 2 — workflow skills  [commit 4a8fc37]
`.claude/commands/`: `process-client-statement`, `reconcile-bank`, `post-journal-entries`
— each wraps the matching `scripts/_*.py` with dry-run-first discipline + verification steps.

### Phase 3 — review + tooling  [commit dd89d41]
- `.claude/agents/financial-reviewer.md` — Opus subagent scoped to sign convention, Decimal,
  balance chain, CRA rules, BQ-write verification
- `.claude/commands/commit.md` — git discipline
- settings: dotnet + Sage50Bridge + `mcp__ide__` permissions

### Phase 4 — silent-write fix  [commit 969bc05]
- `core/bq_loader.py`: `ensure_table` now diffs live schema vs the Pydantic model and adds
  missing NULLABLE columns via `update_table` (closes the silent schema-drift drop, gotcha #10).
  `load_rows` returns the ACTUAL inserted count (0 on exception, n−k on partial fail).
- `tests/bq_loader_smoke.py` — 18/18 offline checks. BigQuery MCP evaluated and SKIPPED
  (the `bq` CLI is already permitted; an MCP adds setup for no real gain).

### Phase 5 — skills #2 + script hygiene  [this commit]
- `.claude/commands/run-tests.md` — runs the offline suite or one named test
- `.claude/commands/onboard-client.md` — registry row → ruleset → smoke test → first dry-run
  (captures the theotherapy onboarding procedure; registry-driven, no daemon change)
- Archived 9 one-off/session-specific diagnostics (`jan_*`, `april_*`, `apr_*`, `_debug_*`)
  → `scripts/archive/` (gitignored from context) with a README. `scripts/` now holds only
  durable tooling.
- CLAUDE.md: documented the `.claude/` tooling; directory map notes archive + durable helpers.

## NEXT STEPS
Year-end worksheet generated; ready for accounting work. Next priorities:

### Immediate accounting tasks (Concetta 2026-04 year-end)
  1. Open `R:\Concetta Enterprises Inc\Year End\concetta_yearend_2026-04.xlsx`
  2. Post adjusting entries in the **2. Adjusting Entries** tab
  3. Review Income Statement and Balance Sheet tabs (formula-driven)
  4. Populate `_CHEQUE_PAYEES` in `sage50/categorization_rules.py` once cheque
     payees are confirmed from live OCR (Rogers, Hydro One, City of Toronto, etc.)

### Pipeline options
  A. Run Feb–Apr 2026 monthly close pipelines (bank statements all processed)
       demo/monthly_close_demo.py --period 2026-0{2,3,4}
  B. Live-validate CHQ payee enrichment: run watcher on a statement with cheques,
       confirm `CHQ#NNNNN - Payee Name` in description and BQ payee field
  C. Onboard a second client: add row to `R:\bookkeeping\client_accounts.csv`,
       no code change needed (registry-driven)
  D. Build Sage 50 ODBC integration (Sage50OdbcAgent live test)
  E. Add T2 corporate tax return agent (PrepareT2ReturnAgent)

Pending cleanup:
  ⚠ Duplicate Dec 2025 journal entries in Sage 50 (J329–J348) — not yet addressed
  ⚠ Trial balance as at 2025-04-30 to verify opening balance $19,077.69 (from Session 11)

Before production:
  ✓ gcloud auth application-default login   (ADC configured 2026-05-07)
  ✓ Gmail OAuth configured                  (send + inbox read, 2026-05-12)
  ⚠ Set Sage 50 ODBC secret:  echo "DSN=...;UID=...;PWD=..." | gcloud secrets versions add vtx-sage50-odbc-conn --data-file=-
  ⚠ Google Chat webhook:       deferred — requires Google Workspace
