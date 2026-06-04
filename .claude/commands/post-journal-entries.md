---
description: "Post verified BQ categorized transactions as balanced BNK journal entries into Sage 50. Always dry-runs first. Sage 50 must be CLOSED."
---

Post journal entries from BigQuery categorized data into Sage 50 using `scripts/_post_je.py`.
Posts from verified BQ data (not re-parsed CSVs) so Sage matches exactly what was reviewed.

Arguments: $ARGUMENTS

## Step 1 — parse arguments

Extract from $ARGUMENTS:
- `--account <xxxx1234>` — masked account_no as stored in BQ (required)
- `--gl-bank <code>` — bank GL display code, e.g. `1060` or `1100` (required)
- `--suspense <code>` — suspense GL for needs_review rows (default `5800`)
- `--sai <path>` — path to the Sage 50 .SAI company file (required for `--commit`)
- `--user <name>` — Sage 50 username (default `sysadmin`)

If `--account` or `--gl-bank` are missing, ask the user before proceeding.

## Step 2 — dry run first (never skip)

```powershell
.venv\Scripts\python.exe scripts\_post_je.py --account <account> --gl-bank <gl-bank> --suspense <suspense>
```

No `--commit` and no `--sai` — this reads BQ only. Review the output:
- **BQ rows** — confirm the count matches expectations for this account
- **Entries built** — should equal BQ rows minus any zero-amount rows
- **Month breakdown** — verify the period distribution looks right
- **Suspense count** — entries for `needs_review` rows; confirm these are expected
- **GL distribution** — review the target GL accounts; flag anything surprising
- **Sample entries** — check a few Dr/Cr pairs make sense (deposit = Dr Bank / Cr GL)
- **balanced: N/N** — must be 100%; any unbalanced entries are a bug, stop immediately

If something looks wrong (unexpected GL, wrong period, suspense count too high): stop and
investigate BQ data before proceeding. Do not post unreviewed data.

## Step 3 — pre-flight checklist

Before running with `--commit`, verify ALL of the following with the user:

- [ ] Sage 50 is **closed** (File → Close Company) — required; writes fail or corrupt data if open
- [ ] The `--account` and `--gl-bank` values match the client's Sage 50 chart of accounts
- [ ] The `needs_review` transactions in BQ have been reviewed (approval_queue checked)
- [ ] The dry-run entry count and period are correct
- [ ] The `.SAI` file path is correct and accessible

Ask: "All checks done — Sage 50 is closed and the numbers look right. Post for real?"

Do NOT proceed without explicit confirmation.

## Step 4 — live post

This command requires explicit user approval (configured in `ask` permissions):

```powershell
.venv\Scripts\python.exe scripts\_post_je.py --account <account> --gl-bank <gl-bank> --suspense <suspense> --sai "<sai_path>" --user <user> --commit
```

After it completes:
- Report: `posted=N total=N errors=N`
- If `errors > 0`: show the FAIL lines and ask whether to retry with `--retry-failed <log>`
- If `errors == 0`: confirm success and remind the user to re-open Sage 50

## Step 5 — verify

After a successful post, open Sage 50 and spot-check:
- Reports → General Journal → filter by Source=BNK and the posting date range
- Confirm entry count matches `posted=N`
- Spot-check 2–3 entries for correct Dr/Cr accounts and amounts

## Notes
- The idempotency guard in `journal_entry.py` (key = date + description[:39] + amount) prevents
  duplicate posts if you re-run. But always confirm with a dry-run first.
- `needs_review` rows post to the suspense GL and must be reclassified in Sage 50 after review.
- The `guard-prod-writes.py` hook logs every `--commit` run to `.claude/prod-writes.log`.
- If a partial failure occurred, use `--retry-failed <log_path>` to re-post only the failed entries.
