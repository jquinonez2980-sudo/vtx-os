---
description: "Onboard a new bookkeeping client: registry row → per-client ruleset → smoke test → first dry-run statement. Registry-driven; no daemon change needed."
---

Onboard a new client into the bank-statement pipeline. Routing is registry-driven:
adding a client is a data change (CSV row) plus an optional per-client categorization
ruleset. No change to `gmail_watcher.py` is required.

Arguments: $ARGUMENTS (client name and/or bank account number, if known)

## Step 1 — gather client facts

Collect (ask the user for anything missing):
- **Client name / `r_folder`** — the folder under `R:\` (e.g. `Canadian Federation of theotherapy`)
- **`client_id`** — short lowercase slug used to select the ruleset (e.g. `theotherapy`)
- **`account_no`** — full bank account number printed on the statement (with or without separators)
- **`gl_bank_account`** — Sage 50 GL display code for the bank account (e.g. `1060`)
- **`bank`** — bank code (TD, RBC, BMO, …)
- **`sender_email`** — optional; the address statements arrive from
- **`year_end_month`** — optional; 1–12 (e.g. 4 = April 30 year-end), 0 if unknown

## Step 2 — add the registry row

Append one row to `R:\bookkeeping\client_accounts.csv` (columns:
`account_no,r_folder,client_id,gl_bank_account,bank,sender_email,year_end_month`).
The account is normalized to digits and keyed on the FULL number — never last-4.

Verify the registry loads and the new client resolves:

```powershell
.venv\Scripts\python.exe -c "from core.client_registry import load_registry; r=load_registry(); print(len(r),'clients'); print([c.client_id for c in r.values()])"
```

Confirm the new `client_id` appears.

## Step 3 — decide on a categorization ruleset

A client with no dedicated ruleset falls back to `DEFAULT_RULES` (everything not matched
→ GL 9999 suspense, needs_review). For a client with recurring named payees, build a
ruleset — it's what makes auto-categorization useful.

If building one:
1. Derive the rules from the client's prior-year **General Ledger** (the source of truth
   for which payee maps to which GL account). Ask the user for the GL export if not provided.
2. Add a `<Client>Ruleset` class in `sage50/categorization_rules.py`, mirroring
   `TheotherapyRuleset` / `ConcettaRuleset`:
   - `categorize(description, amount) -> (gl_no: int, gl_name: str, confidence: Decimal)`
   - High-confidence vendor matches ≥ 80 (auto-approve); named-person/payroll < 80 (stays in review)
   - Unmatched → suspense GL, confidence 0
   - Mind rule ordering (specific beats generic: fee beats tithes, auto-insurance beats generic insurance)
3. Register it in `_CLIENT_RULESETS` (keyed by `client_id`).

## Step 4 — write and run a categorization smoke test

Create `tests/<client_id>_categorization_smoke.py`, modelled on
`tests/theotherapy_categorization_smoke.py`:
- A `(description, amount, expected_gl)` case table covering every rule
- Ordering edge cases (the rules that must beat each other)
- Confidence-threshold checks (auto vs review)
- `get_ruleset('<client_id>')` returns the right class

Run it green before proceeding:

```powershell
.venv\Scripts\python.exe tests\<client_id>_categorization_smoke.py
```

Then run the full offline suite (`/run-tests`) to confirm no regressions.

## Step 5 — first statement, dry-run first

Process the client's first real statement via `/process-client-statement` (or directly):

```powershell
.venv\Scripts\python.exe scripts\_process_one.py --match "<subject substring>" --dry-run
```

Confirm the statement **auto-routes** to the new client (account resolved from the
statement text), the period is correct, and transactions parse. Only then book for real.

## Step 6 — record it

Add a short note to `PROJECT_STATUS.md` (new client onboarded: id, account masked,
ruleset yes/no, first period processed). Commit registry-adjacent code changes
(ruleset + test) with `/commit`; the CSV lives on `R:\` and is not version-controlled.

## Notes
- The CSV is the single onboarding lever — the daemon picks up new clients with no code change.
- Two *different* registered clients appearing on one statement → quarantined as ambiguous
  (never mis-booked). A single account repeating is fine.
- Non-TD banks need no code change for routing (account search is bank-agnostic), but
  `extract_account_no`/period detection are TD-tuned — generalize per bank as needed.
