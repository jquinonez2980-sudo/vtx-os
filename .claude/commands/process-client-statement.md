---
description: "Process a single client bank-statement PDF from Gmail inbox: OCR → route → book. Always dry-runs first."
---

Process one bank-statement email from the Gmail inbox using `scripts/_process_one.py`.
Uses the same OCR → routing → booking pipeline as the live daemon.

Arguments: $ARGUMENTS

## Step 1 — parse arguments

Extract from $ARGUMENTS:
- `--match <substring>` — case-insensitive subject match (required)
- `--period YYYY-MM` — period override; otherwise auto-detected from statement text
- `--client <id>` — client pin (optional; normally auto-routed by account number on the statement)

If `--match` was not provided, ask the user for the subject substring before proceeding.

## Step 2 — dry run first (never skip)

```powershell
.venv\Scripts\python.exe scripts\_process_one.py --match "<match>" [--period <YYYY-MM>] --dry-run
```

Review the output and confirm all of the following before continuing:
- **Registry loaded** — at least 1 client account loaded from `R:\bookkeeping\client_accounts.csv`
- **Matched subject** — the correct email was selected
- **Client routed** — account number resolved to the expected client (e.g. `Concetta Enterprises Inc`)
- **Period detected** — matches the statement month (e.g. `2025-01`)
- **Transactions parsed** — non-zero; TD statements typically 20–40 txns
- **No errors** — no `[ERROR]` lines or `unrouted` warnings

If the client shows **unrouted**: the account number is not in the registry CSV. Stop and
tell the user to add the client row to `R:\bookkeeping\client_accounts.csv` first.

If the period is wrong: re-run the dry-run with an explicit `--period YYYY-MM`.

## Step 3 — confirm before booking

Present the dry-run summary and ask:
> "Dry run looks good — {n} transactions for {client}, period {period}. Book for real?"

Do NOT proceed without explicit confirmation.

## Step 4 — live run

```powershell
.venv\Scripts\python.exe scripts\_process_one.py --match "<match>" [--period <YYYY-MM>]
```

After it completes, confirm:
- CSV written to `R:\{client_folder}\drop\`
- Transactions uploaded to GCS and booked via BookkeepingAgent
- Email marked as read (only happens when all attachments book successfully)

## Notes
- Idempotent: sha256 dedup in bank_parser makes re-running the same statement safe.
- Already-read emails won't appear in the inbox poll — use `gmail_watcher.py --once` to reprocess.
- The `guard-prod-writes.py` hook logs this run; check `.claude/prod-writes.log` if something looks off.
