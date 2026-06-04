---
description: "Reconcile statement CSVs against their balance column. Classifies SIGN_FLIP (auto-fixable) vs GAP (needs PDF review)."
---

Reconcile all statement CSVs in a drop directory against their own balance column
using `scripts/_reconcile.py`. The balance column is ground truth — never trust the
parsed amount alone.

Arguments: $ARGUMENTS

## Step 1 — parse arguments

Extract from $ARGUMENTS:
- `--dir <path>` — directory containing statement CSVs (required; usually `R:\<client>\drop`)
- `--out <path>` — optional path to write auto-fixable corrections CSV
- `--review-out <path>` — optional path to write a full fill-in review worksheet (recommended)

If `--dir` was not provided, ask the user for the drop directory before proceeding.

Default the review worksheet to `<dir>\_review.csv` if `--review-out` was not given.

## Step 2 — run reconciliation

```powershell
.venv\Scripts\python.exe scripts\_reconcile.py --dir "<dir>" --review-out "<dir>\_review.csv" [--out "<dir>\_corrections.csv"]
```

## Step 3 — interpret results

The script classifies discrepancies into two types:

**SIGN_FLIP** — a single row whose sign, when flipped, makes the segment balance exactly.
- Balance-proven: the correct value is the negated amount.
- Safe to accept; the `--out` corrections CSV captures these.
- Show the user each one: date, old amount → new amount, description.

**GAP** — a segment where no single sign flip reconciles the balance delta.
- Indicates a missing or garbled row; must be verified against the original PDF.
- Do NOT auto-fix. Show the user each gap: date range, parsed sum vs balance delta, difference.
- For each GAP, tell the user: "Check the PDF between {from_date} and {to_date} — there may be a missing transaction (off by {diff})."

## Step 4 — summarise and recommend

Present a table:

| File | SIGN_FLIPs | GAPs |
|------|-----------|------|
| ... | ... | ... |

Then:
- If SIGN_FLIPs > 0 and `--out` was given: "Auto-corrections written to `_corrections.csv`. Apply these to BQ with the `_book_one.py` or re-parse step."
- If GAPs > 0: "These gaps need manual PDF review before booking."
- If both are 0: "All segments balance — no corrections needed."

## Notes
- Rows with `_`-prefixed filenames are skipped (helper files like `_corrections.csv`).
- A perfectly balanced statement has 0 SIGN_FLIPs and 0 GAPs — that is the goal.
- The balance column comes from the bank's own statement; it is always authoritative over OCR-parsed amounts.
- After fixing, re-run reconciliation to confirm 0 discrepancies before posting to Sage 50.
