# CLAUDE.md — Vertex AI Accounting OS (vtx-os)

> **Every session: read `PROJECT_STATUS.md` first.** It tracks the current phase,
> completed steps, and exact next action. This file provides persistent context;
> PROJECT_STATUS.md provides current state.

---

## Mission

Production-grade Python accounting platform for Canadian SMBs. Automatically
extracts, normalises, and loads bank transactions from PDF statements and Sage 50
CSV/ODBC exports into BigQuery for GL reconciliation, HST/GST preparation, and
human-supervised approval workflows.

**GCP project:** `vtx-accounting-os-prod` (northamerica-northeast2)  
**Auth:** ADC configured (jquinonez2980@gmail.com)  
**Python:** 3.14.4 — venv at `.venv/`  

---

## Current Phase (always verify in PROJECT_STATUS.md)

Phase 2 — Multi-Agent ADK Architecture  
P2.1 ✅ Orchestrator + Supervisor agent + ADK runtime  
P2.2 ⏳ A2A protocol wiring ← **next**  
P2.3 ⏳ Gmail Comms agent  
P2.4 ⏳ Eventarc trigger (GCS object finalize → orchestrator)  
P2.5 ⏳ RAG agent  
P2.6 ⏳ Engagement letter + monthly close demo  
P2.7 ⏳ Full monthly close — one real client  

Early accounting-agent code exists (GL recon, ODBC loader, HST stubs) but is **not yet
integrated into the ADK runtime** — treat as library code until P2.1–P2.2 are done.

---

## Architecture

```
[Phase 2 target — ADK runtime]
SupervisorAgent (ADK)
    └── OrchestratorAgent (ADK)
            ├── BookkeepingAgent      [BOOKKEEPING_RUN]
            ├── Sage50IngestAgent     [INGEST_SAGE50_CSV]
            ├── Sage50OdbcAgent       [INGEST_SAGE50_ODBC]
            ├── ReconcileGLAgent      [RECONCILE_GL]       ← built early
            ├── PrepareHSTReturnAgent [PREPARE_HST_RETURN] ← stub exists
            ├── GmailCommsAgent       [SEND_CLIENT_EMAIL]  ← P2.3
            └── (RAG agent, T2 agent, etc.)

Inter-agent communication: A2A protocol (P2.2)
Trigger: Eventarc GCS object.finalize → orchestrator (P2.4)

Data flow (current — pre-ADK):
  PDF/CSV → sage50/pdf_extractor → sage50/bank_parser
         → sage50/categorizer → BQ (raw + categorized)
         → core/approval_queue → core/chat_notifier
         → vtx_audit.audit_log (every step)
```

**Core patterns:**
- `TaskRequest` → `AgentBase.run()` → `handle()` → `TaskResult`
- Every agent call emits AGENT_START + AGENT_COMPLETE audit rows
- BQ writes fall back to stderr JSON — audit events are **never silently lost**
- Session ID propagates from orchestrator to all sub-agents (traceability)
- Secret Manager cached in-process; env var `VTX_SECRET_<NAME>` bypasses GCP for local dev

---

## Directory Map

```
agents/
  base.py               AgentBase, TaskType (12 types), TaskRequest, TaskResult
  orchestrator.py       OrchestratorAgent — dispatcher, registry, 7-event audit trail
  supervisor.py         SupervisorAgent (ADK LlmAgent) — natural language → dispatch_task tool
  adk_runner.py         ADK Runner + InMemorySessionService; run_sync() entry point
  bookkeeping.py        BookkeepingAgent  — parse → categorize → BQ → queue → Chat
  sage50_ingest.py      Sage50IngestAgent — CSV export → GCS upload
  sage50_odbc.py        Sage50OdbcAgent   — ODBC → BQ for all 10 Sage 50 report types
  reconcile_gl.py       ReconcileGLAgent  — GL recon (built early; not yet in ADK graph)
  prepare_hst_return.py PrepareHSTReturnAgent — HST return (stub, not registered, untested)

core/
  audit.py         BQ streaming writer; stderr fallback; never silent
  bq_loader.py     schema_from_model(), ensure_table(), load_rows()
  secrets.py       Secret Manager client + thread-safe in-process cache
  approval_queue.py BQ-backed queue: submit/approve/reject/escalate via DML
  chat_notifier.py Google Chat incoming webhook, Cards v2, graceful degradation

models/
  base.py          Severity, EventType (20), EventStatus, AgentEvent, AuditRecord
  banking.py       BankCode, BankTransaction, CategorizedTransaction, CategorizationRule
  sage50.py        10 Sage 50 row types; all use Decimal amounts + date parsing
  approval.py      ApprovalItem, ApprovalStatus

sage50/
  pdf_extractor.py OCR-resistant TD Bank PDF → TD-format CSV; balance-chain correction
  bank_parser.py   Auto-detects 7 Canadian bank CSV formats; sha256 dedup
  categorizer.py   29 regex rules, Canadian context; confidence scoring
  csv_uploader.py  GCS upload with ReportType enum + lifecycle folder structure
  odbc_reader.py   Sage 50 ODBC queries; column constants; Pydantic conversion

tests/
  p1_7_e2e.py              Offline E2E, mock BQ, 62/62 checks (Northview Dec 2025)
  concetta_live_pipeline.py Live GCP E2E, 6/6 checks (Concetta Dec 2025)

data/test-client/          Gitignored — real client PDFs, CSVs, BQ previews
config/                    Templates only; never commit actual .env or *.json
scripts/                   GCP setup scripts (idempotent)
PROJECT_STATUS.md          Single source of truth for current phase + decisions
```

---

## Critical Conventions

### Money — use Decimal, never float

```python
from decimal import Decimal, InvalidOperation

amount = Decimal("1234.56")   # always string constructor
```

- All monetary Pydantic fields: `Decimal`, serialized as `str` in BQ (`mode="json"`)
- BQ schema: `Decimal → NUMERIC` via `schema_from_model()`
- CRA requires 2-decimal-place precision — never round intermediate values

### Sign Convention

**Positive amount = money IN (deposit, credit, receipt)**  
**Negative amount = money OUT (withdrawal, debit, payment)**

The `bank_parser` computes: `amount = deposits - abs(withdrawals)`

The `abs()` on withdrawals is intentional and critical. TD Bank CSVs store withdrawals
as positive numbers in the "Withdrawals ($)" column. Without `abs()`, a CSV that
accidentally has negative withdrawals would flip signs and produce phantom deposits.
This bug was discovered during P1.7 — always use `abs()` when reading debit/outflow columns.

### Bank CSV Column Formula (all 6 bank parsers)

```python
amount = credit_column - abs(debit_column)
```

Never deviate from this pattern. If a new bank format is added, apply the same rule.

### Date Handling

- **Output** (pdf_extractor → CSV): always `strftime("%Y-%m-%d")` — ISO 8601, unambiguous
- **Input** (bank_parser `_parse_date`): tries `%m/%d/%Y`, `%Y-%m-%d`, `%d/%m/%Y`, etc.
- **Sage 50 models** (`_date()` validator): handles `MM/DD/YYYY`, `YYYY-MM-DD`, `DD-Mon-YYYY`
- Never output DD/MM/YYYY — bank_parser reads MM/DD/YYYY first and will misread December dates

### Audit Trail

`core/audit.py` must never be bypassed. Every agent action writes to `vtx_audit.audit_log`.
If BQ is unreachable the record prints to stderr as JSON. There is no silent failure path.

To log manually:
```python
from core.audit import write_event
from models.base import AuditRecord
write_event(AuditRecord.ok(agent_id="my-agent", event_type=EventType.BQ_LOAD_COMPLETE, ...))
```

### Error Handling Philosophy

- External services (BQ, GCS, Secret Manager, Chat webhook): catch + degrade gracefully
- Internal logic (parsing, models, validation): let exceptions propagate — they indicate bugs
- GCS pipeline failures: move file to `sage50/failed/` with `.error.txt` sidecar
- Never use `except Exception: pass` — always log or re-raise

---

## OCR / PDF Domain Knowledge (TD Bank Canada)

### Known OCR Artifacts

| Pattern in PDF | Meaning | Fix |
|---|---|---|
| `DE.C31`, `DECO!`, `DECll`, `DECIO` | DEC01, DEC11, DEC10 | `_preprocess_line()` + `_OCR_DIGIT_MAP` |
| `·DEC04`, `•JAN12` | DEC04, JAN12 | Strip leading bullets |
| `651,40` | $651.40 (comma decimal) | `re.fullmatch(r"\d+,\d{2}", s)` → replace `,` with `.` |
| `430 .14` | $430.14 (space before decimal) | Remove spaces before parsing |
| `23., 249. 07` | $23,249.07 (large credit, badly split) | Balance-chain correction |
| `·9:4 .92` | Garbled (unrecoverable OCR) | Balance-chain residual |
| `. SERVI CE-'-CHAR.GE` | SERVICE CHARGE | OCR too garbled for date detection — tolerable loss |

### Balance-Chain Correction Algorithm

Statement layout: `DESCRIPTION  AMOUNT  DATE  BALANCE`

Algorithm:
1. Parse each line: find date token, split text before date into description + amount
2. If `_parse_amount()` fails (garbled), set `parsed_amount = None`
3. Group transactions between consecutive stated balances
4. For each group: `expected_change = running_balance - stated_balance`
5. Fill `None` amounts as residual: `residual = abs(expected_change) - known_sum`
6. For single-item groups: if `|parsed - expected| > $0.50`, override with `abs(expected_change)`
7. Credit detection: `expected_change < 0` → `is_credit = True`

Key constant: `_BALANCE_TOLERANCE = Decimal("0.50")` — max acceptable OCR rounding error.

`_parse_amount()` validity gates:
- Must have exactly 2 decimal places (`re.search(r"\.\d{2}$", s)`)
- Must be `>= $1.00` (eliminates most noise)

### TD Bank PDF Page Structure

- Page 1: transaction text (OCR'd)
- Page 2+: scanned cheque images (skip)
- Filter: only include pages containing "BALANCE FORWARD" or "DESCRIPTION"

---

## Canadian Banking Domain Knowledge

### Supported CSV Formats (auto-detected)

| Bank | Key Header Tokens |
|---|---|
| RBC | `transaction date`, `description 1`, `cad$` |
| TD | `withdrawals ($)`, `deposits ($)` |
| Scotiabank | `funds out`, `funds in`, `transaction` |
| Desjardins | `withdrawal`, `deposit`, `no` |
| CIBC | `debit`, `credit`, `description` |
| BMO | `withdrawal`, `deposit`, `description` |
| National Bank | `withdrawals`, `deposits`, `description` |

TD and BMO have overlapping headers — Desjardins is distinguished by the `no` (transaction number) column.

### Categorization Rules (29 rules, `sage50/categorizer.py`)

Rules cover Canadian-specific payees:
- `ADP`, `CERIDIAN`, `PAYWORKS` → GL 5100 (Salaries)
- `RECEIVER GENERAL` / `CRA` + HST/PAYROLL → GL 2100/2200
- `HYDRO ONE`, `ENBRIDGE`, `BELL`, `ROGERS`, `TELUS` → GL 5500 (Utilities)
- `INTACT`, `AVIVA`, `WAWANESA` → GL 5300 (Insurance)
- `INTERAC`, `E-TRANSFER`, `WIRE` → GL 9999 (Transfer — needs review)
- Unmatched → GL 9999, confidence 0.0, needs_review = True

Confidence thresholds: `0.95` (matched rule), `0.60` (transfer), `0.0` (unmatched)  
Auto-approve threshold: `0.80` (configurable per invocation)

Cheques (`CHQ#...`), PC Mastercard, AMEX payments are **not** in the default ruleset
and will always go to needs_review. This is expected for clients like Concetta Enterprises.

---

## GCP Quick Reference

```
Project:   vtx-accounting-os-prod
Region:    northamerica-northeast2

BQ Datasets:
  vtx_audit.audit_log                         day-partitioned, clustered agent_id/event_type
  vtx_accounting.bank_transactions_raw        partitioned txn_date, clustered bank_code/account_no
  vtx_accounting.bank_transactions_categorized partitioned txn_date, clustered bank_code/gl_account_no
  vtx_accounting.approval_queue               partitioned txn_date, clustered status/bank_code
  vtx_accounting.{gl,ar,ap,coa,customers,     all created lazily by ensure_table()
                  vendors,inventory,payroll,
                  tax_summary,bank_reconciliation}

GCS Bucket: vtx-accounting-os-prod-vtx-exports
  sage50/{raw|staging|archive|failed}/YYYY/MM/DD/{report_type}/

Secrets (all placeholders except webhook):
  vtx-sage50-odbc-conn       vtx-sage50-company-path
  vtx-cantax-api-key         vtx-gmail-oauth-credentials
  vtx-google-chat-webhook
```

Set a secret:
```
echo "value" | gcloud secrets versions add <secret-name> --data-file=-
```

Local dev override (bypasses Secret Manager):
```
VTX_SECRET_VTX_SAGE50_ODBC_CONN="DSN=Sage50;UID=...;PWD=..."
```

---

## Testing Patterns

### Offline E2E (mock BQ)

Inject the mock before importing agents so singletons are replaced:

```python
def _inject_mock(client):
    import core.bq_loader, core.audit, core.approval_queue
    core.bq_loader._client         = client
    core.audit._client             = client
    core.approval_queue._bq_client = client
```

`MockBQClient` must check `"UPDATE"` before `"PENDING"` in its `query()` method —
UPDATE DML SQL contains `AND status = 'PENDING'` in the WHERE clause, so checking
PENDING first routes DML to the wrong handler. See `tests/p1_7_e2e.py`.

### Live GCP E2E

Use `tests/concetta_live_pipeline.py` as the template. Requires ADC configured.
Never write live tests that mutate production data without a cleanup step.

### Adding a New Agent

1. Subclass `AgentBase`, set `agent_id`, implement `handle()`
2. Add a `TaskType` constant to `agents/base.py`
3. Import in `agents/orchestrator.py` (registry is class-level, auto-populated)
4. Add offline mock test + one live spot-check

---

## Collaboration Workflow (How to Work with Claude)

### Session Start

1. Say: "Read PROJECT_STATUS.md and confirm current phase."
2. State the single task for this session.
3. For non-trivial changes: ask for a plan first, approve, then implement.

### When to Use /plan

Use plan mode for:
- Any change to financial calculation logic (sign handling, Decimal precision, balance chain)
- New agent or new TaskType
- Schema changes to BQ tables (irreversible)
- Changes that touch more than 3 files

Skip plan mode for:
- Adding a categorization rule
- Updating PROJECT_STATUS.md
- Writing or extending a test
- Fixing a clearly identified bug in one function

### Token Efficiency

- `.claudeignore` excludes `.venv/`, `data/`, bytecode, and credentials
- PROJECT_STATUS.md is the lightweight session-to-session state; CLAUDE.md is the deep reference
- For long sessions: compact context after completing a self-contained step
- When asking for help on a specific module, paste the relevant function rather than asking Claude to search

### Financial Logic — Extra Rigour Required

For any change touching:
- `_parse_amount()`, `_parse_balance()`, `_resolve_amounts()` in `pdf_extractor.py`
- Amount formula in any `_parse_*` function in `bank_parser.py`
- `schema_from_model()` Decimal→NUMERIC mapping in `bq_loader.py`
- Any CRA remittance categorization rule

Always:
1. Write the change
2. Manually trace through at least one real example (use Concetta or Northview data)
3. Verify the balance chain holds end-to-end before committing

### Git Discipline

- One logical change per commit
- Commit messages: imperative mood, explain *why* not what
- Never commit: `data/test-client/`, `config/project.env`, `config/*.json`, `.env`
- Update PROJECT_STATUS.md in the same commit as the feature it describes

---

## Known Gotchas

1. **`\b?` on zero-width assertions**: Illegal in Python 3.12+. Use `\b` alone or remove.
2. **DD/MM/YYYY output**: bank_parser tries MM/DD/YYYY first — always output %Y-%m-%d from pdf_extractor.
3. **Mock branch ordering**: In MockBQClient.query(), check `"UPDATE" in sql` BEFORE `"PENDING" in sql`.
4. **Decimal in JSON**: Use `model_dump(mode="json")` to serialise TaskResult.output — Decimal is not JSON-native.
5. **AuditRecord metadata**: Use `_SafeEncoder` when serialising metadata dicts containing Decimal or date.
6. **Approval queue DML**: `UPDATE` SQL contains `AND status = 'PENDING'` in WHERE — don't confuse with SELECT.
7. **pdfplumber page 2**: TD Bank PDFs have cheque scan images on page 2 — filter by pages containing "BALANCE FORWARD".
8. **SERVICE CHARGE OCR loss**: Completely garbled lines where the date cannot be found are silently dropped. Acceptable — document in test output.
