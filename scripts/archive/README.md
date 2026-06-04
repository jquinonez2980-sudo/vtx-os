# scripts/archive/

One-off, session-specific diagnostic and remediation scripts. Kept for history
and reference, **not** active tooling. This folder is excluded from Claude Code's
context (`.claudeignore`) so it doesn't bloat searches.

These were written to diagnose or remediate a specific statement/period and are
not meant to be re-run as-is. The durable equivalents live in `scripts/` proper:

| Archived (one-off)                | Durable replacement / context              |
|-----------------------------------|--------------------------------------------|
| `jan_*` (Jan 2026 purge/recon)    | `scripts/purge_from_csv.py`, `_reconcile.py` |
| `april_*`, `apr_reconcile_verify` | `_reconcile.py`, `apr` period in the demo  |
| `_debug_feb_ocr.py`, `_debug_pdf.py` | `sage50/statement_extractor.py` (`benchmark`) |

If you need one of these again, copy the logic into a durable, parameterized tool
rather than re-running the dated script.
