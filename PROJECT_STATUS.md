# PROJECT_STATUS.md — Vertex AI Accounting OS
# Updated: 2026-05-22  |  Session: 11  (Concetta 2026.SAI data cleanup)
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
  - sage50/bank_parser.py:   abs() applied to withdrawal/debit columns (all 6 parsers)
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

## NEXT STEPS
Phase 2 complete. Phase 3 options:
  A. Re-run monthly close demo with live Gmail send (OAuth now configured)
       python demo/monthly_close_demo.py
  B. Build Sage 50 ODBC integration (Sage50OdbcAgent live test)
  C. Build SupervisorAgent natural-language dispatch for full close workflow
  D. Add T2 corporate tax return agent (PrepareT2ReturnAgent)
  E. Add year-end close workflow (YEAR_END_CLOSE TaskType)

Before production:
  ✓ gcloud auth application-default login   (ADC configured 2026-05-07)
  ✓ Gmail OAuth configured                  (send + inbox read, 2026-05-12)
  ⚠ Set Sage 50 ODBC secret:  echo "DSN=...;UID=...;PWD=..." | gcloud secrets versions add vtx-sage50-odbc-conn --data-file=-
  ⚠ Google Chat webhook:       deferred — requires Google Workspace
