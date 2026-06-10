# AcumenAI (vtx-os) — Autonomous Accounting OS for Canadian SMBs

Multi-agent platform that turns emailed bank statements into reviewed, posted
general-ledger entries: Gmail ingestion → OCR (PyMuPDF → pdfplumber → Document AI)
→ rule-based categorization → human review on the AcumenAI dashboard → posting to
Sage 50 (QuickBooks Online connector in progress). GST/HST tracking, reconciliation,
and year-end worksheets ride the same pipeline. Ships as **AcumenAI by Orchelix**.

**Status:** early production — live clients, real money. Treat every posting path
as hot. Dashboard: https://acumenai-api-lscziarcxa-pd.a.run.app

## Where the real documentation lives

| Doc | Read it for |
|---|---|
| [`PROJECT_STATUS.md`](PROJECT_STATUS.md) | Current state, session log, exact next actions — **read first** |
| [`CLAUDE.md`](CLAUDE.md) | Deep reference: architecture, conventions, domain gotchas, commands |
| [`docs/BOOKKEEPER_GUIDE.md`](docs/BOOKKEEPER_GUIDE.md) | Operational runbook (statement → books, new client onboarding) |

## Quick start (Windows / PowerShell)

```powershell
.\.venv\Scripts\Activate.ps1                         # venv (Python 3.14)
python tests\test_smoke_suite.py                     # offline suite — no GCP auth needed
python scripts\gmail_watcher.py --once --dry-run     # live pipeline, zero writes
```

Live operations need ADC (`gcloud auth application-default login`) and write to
production BigQuery — see CLAUDE.md before running anything with `--commit`.

## Non-negotiable conventions

- **Money is `Decimal`** (string constructor), never float. BQ NUMERIC, CRA 2-dp.
- **Sign:** `amount = credit − abs(debit)`. Positive = money in. All 7 bank parsers.
- **The balance column is ground truth** — validate signs against the balance chain
  before booking or posting.
- **Audit is never lost:** every agent action → `vtx_audit.audit_log`, stderr JSON fallback.
- Sage 50 must be **closed** during posting; back up `.SAI`/`.SAJ` first (tooling does this).
