# AcumenAI — Investor Demo Runbook

A rehearsable, ~90-second live demo. Built for **reliability first**: the primary
demo runs fully offline on fictional data — no network, no cloud auth, no live
client data — so it cannot fail mid-pitch. An optional "wow" tier shows the real
email pipeline when conditions allow.

> **Golden rule:** rehearse the primary demo until you can run it without looking.
> Never live-debug in front of investors — if anything is off, fall back (below).

---

## 0 · One-time setup (do this before the meeting)

```powershell
cd C:\Users\JorgeJr\vtx-os
.\.venv\Scripts\Activate.ps1
python scripts\demo_run.py            # confirm it runs clean
```

You should see five beats print and end with **"WHAT JUST HAPPENED"** in well
under a second. If it runs clean once, it will run clean live — there is no
external dependency. Maximize the terminal font (Ctrl + Scroll) so the room can read it.

---

## 1 · Primary demo — the 90-second script

One command. Talk over it; the output is paced for narration.

```powershell
python scripts\demo_run.py --approve
```

| Beat | What's on screen | What you say (≈15s each) |
|---|---|---|
| **Ingest** | 20 transactions parsed, money in/out | *"A client emails their monthly bank statement. AcumenAI reads it — here, 20 transactions, and it auto-detected the bank format. No one typed any of this."* |
| **Verify** | Balance chain reconciled 19/19 | *"This is our edge. We check every transaction against the bank's own running balance — math, not guesswork. It catches the sign errors and missing rows that sink manual bookkeeping."* |
| **Categorize & Queue** | 12/20 auto-categorized, 8 to review, queued | *"Each transaction is assigned to the right account with a confidence score. The clear ones are done automatically; the judgment calls are queued for a human."* |
| **Audit** | 7 immutable audit events | *"Every step is logged to an immutable trail. Nothing fails silently — that's the compliance backbone an accounting firm needs."* |
| **Approve** | One item approved, queue 8 → 7 | *"The bookkeeper reviews exceptions with one click. They approve the work — they don't do the data entry. One person can now handle 200 clients instead of 40."* |

**Close on the recap line:** *"A bank statement became reviewed, categorized,
balance-verified books — with a full audit trail — in 8 milliseconds."*

### If they ask "is that real or canned?"
- It's the **real pipeline** — the same agents, parser, categorizer, balance-chain
  verifier, and approval queue that run on live client books. Only the **data** is
  fictional (a sample statement) and the **infrastructure is mocked** so it runs
  anywhere without touching a real client's records.
- The 60% auto-categorization is on **default rules with zero tuning**. With a
  client-specific ruleset — which the system learns per client — that climbs past
  90%, as we've seen on a real client.

---

## 2 · Optional "wow" tier — the live email pipeline

Only if you have network + the Gmail token is fresh + you've rehearsed it. This
shows a statement auto-routing from a real inbox. It is **dry-run** (no writes).

```powershell
python scripts\gmail_watcher.py --once --dry-run
```

Narrate: *"In production it's even simpler — the client just emails the statement.
The system pulls it from the inbox, reads the account number off the page, and
routes it to the right client automatically. Watch."*

**Pre-flight (do before the meeting, not live):**
- Confirm a sample statement email is sitting unread in the inbox.
- Run the command once to confirm the OAuth token is valid (it expires; re-auth
  with `python scripts\gmail_auth.py` if needed).
- If anything stalls, **drop this tier entirely** and stay with the primary demo.

---

## 3 · Fallback (if a screen/network/laptop fails)

1. **Screenshot deck** — keep a PDF of a clean `demo_run.py --approve` run on your
   phone and laptop. Walk through the five beats from the screenshots.
2. **The one-pager** — `docs/investor-onepager.html` (Proof section) carries the
   same numbers. Pivot to it and keep talking.
3. Never apologize for tooling or debug live. The story is the product, not the terminal.

---

## 4 · Anticipated questions → crisp answers

- **"What's defensible?"** → Per-client learned rulesets (compounding switching
  cost) + an immutable audit trail (the compliance moat) + balance-chain
  verification (accuracy competitors using pure-LLM extraction can't match).
- **"Does it touch the GL directly?"** → Yes — approved entries post into Sage 50
  via our bridge. The human approves; the system posts.
- **"What about wrong categorizations?"** → Anything below the confidence threshold
  is never auto-booked — it's queued for review. Errors surface as review items,
  not as silent mis-postings.
- **"Scanned PDFs?"** → Handled — a three-tier extractor falls back to OCR for
  image-only statements (proven on an 18 MB scanned statement at 100% recall).

---

## Notes for the operator
- Primary demo data: `demo/sample_statement.csv` (fictional Northview Consulting;
  committed, safe to show). Driver: `scripts/demo_run.py`.
- The demo writes nothing and needs no credentials. Re-run as many times as you
  like; state resets each run.
- Keep real client names (Concetta, etc.) **off-screen** — close other terminals,
  editors, and the BigQuery console before presenting.
