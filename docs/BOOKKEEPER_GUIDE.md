# VTX Accounting OS — Bookkeeper's Guide

**Version:** 1.0  &nbsp;|&nbsp; **Prepared by:** Jorge Quinonez CPA  &nbsp;|&nbsp; **Updated:** June 2026

> This guide takes you from turning on your computer all the way through
> running a monthly close and adding a brand-new client.
> No programming experience required.

---

## Table of Contents

1. [Understanding the System](#1-understanding-the-system)
2. [First-Time Computer Setup](#2-first-time-computer-setup)
3. [Opening the Program Every Day](#3-opening-the-program-every-day)
4. [Monthly Close — Existing Client](#4-monthly-close--existing-client)
5. [Year-End Worksheet](#5-year-end-worksheet)
6. [Adding a New Client](#6-adding-a-new-client)
7. [Troubleshooting](#7-troubleshooting)
8. [Quick Reference Card](#8-quick-reference-card)

---

## 1. Understanding the System

Before touching the keyboard, it helps to know what the pieces are.

```
┌─────────────────────────────────────────────────────────────────┐
│                      YOUR COMPUTER                              │
│                                                                 │
│  ┌──────────────────┐    ┌───────────────────────────────────┐  │
│  │   Sage 50        │    │  VTX Accounting OS (vtx-os)       │  │
│  │  (must be open   │◄──►│  The program you run from the     │  │
│  │   to post)       │    │  black command window             │  │
│  └──────────────────┘    └──────────────┬────────────────────┘  │
│                                         │                        │
└─────────────────────────────────────────┼────────────────────────┘
                                          │
              ┌───────────────────────────▼──────────────────────┐
              │                    R:\ Drive                      │
              │   (network drive — all client folders live here)  │
              │                                                   │
              │   R:\Concetta Enterprises Inc\drop\               │
              │   R:\Arrow Mechanical Services Inc\drop\          │
              │   R:\bookkeeping\client_accounts.csv  ◄── master  │
              └──────────────────────────────────────────────────┘
```

**The R: drive** is the shared drive where all client folders live.
Each client has a `drop\` subfolder — this is where bank statement files go in,
and where export CSVs come out.

**VTX-OS** reads the bank statement, categorizes every transaction, posts
journal entries to Sage 50, and produces the month-end close package automatically.

**Sage 50** holds the official books. VTX-OS writes into it; you review in it.

---

## 2. First-Time Computer Setup

> **Do this only once.** If someone has already done these steps on this computer,
> skip to Section 3.

### 2.1 — Check the R: Drive Is Mapped

1. Open **File Explorer** (the folder icon on your taskbar, or press `Win + E`).
2. Look in the left panel under **This PC** for a drive labelled **R:**.
3. If you see `R:\` — great, skip to step 2.2.
4. If R: is missing, ask your IT person to map the network drive. The program
   will not work without it.

### 2.2 — Verify the Program Folder Exists

1. In File Explorer, navigate to:
   ```
   C:\Users\JorgeJr\vtx-os
   ```
2. You should see folders named `scripts`, `sage50`, `agents`, `demo`, etc.
3. If the folder is missing, contact Jorge — the program needs to be installed first.

### 2.3 — One-Time Google Account Authorization

The program reads bank statements from Gmail and posts results to Google Cloud.
This authorization step only needs to happen once per computer.

1. Open **PowerShell** (see Section 3.1 for how).
2. Type each line below and press **Enter** after each:

```powershell
cd C:\Users\JorgeJr\vtx-os
.venv\Scripts\activate
python scripts/gmail_auth.py
```

3. A browser window will open. Sign in with the firm's Google account
   (`jquinonez2980@gmail.com`).
4. Click **Allow** on the permissions screen.
5. You will see `Authorization complete. Credentials saved.` in the black window.
6. Close the browser and return to PowerShell.

> **You only do this once per computer.** Credentials are stored securely in
> Google Cloud Secret Manager — never on your hard drive.

---

## 3. Opening the Program Every Day

Every time you sit down to work, do these three steps first.

### Step 1 — Open PowerShell

1. Click the **Start menu** (Windows icon, bottom-left).
2. Type `PowerShell` in the search bar.
3. Click **Windows PowerShell** in the results.
4. A black window with a blue/dark background will appear. This is where you type commands.

```
  ┌────────────────────────────────────────────────────┐
  │  Windows PowerShell                            — □ X│
  │                                                     │
  │  PS C:\Users\JorgeJr>  _                           │
  │                                                     │
  └────────────────────────────────────────────────────┘
```

### Step 2 — Navigate to the Program Folder

Type this and press **Enter**:

```powershell
cd C:\Users\JorgeJr\vtx-os
```

The prompt will change to show `vtx-os>`.

### Step 3 — Activate the Environment

Type this and press **Enter**:

```powershell
.venv\Scripts\activate
```

You will see `(vtx-os)` appear at the start of the line. This confirms the
program's tools are loaded.

```
  (vtx-os) PS C:\Users\JorgeJr\vtx-os>  _
```

> **You must see `(vtx-os)` before running any program commands.**
> If you close the window and re-open it, you must repeat Steps 2 and 3.

---

## 4. Monthly Close — Existing Client

This is the most common task. Run it after each month's bank statement arrives.

### What you need before starting

| Item | Where to find it |
|------|-----------------|
| Bank statement file (CSV format) | Exported from the bank's website, or automatically received via email |
| Sage 50 open with the client's company file | Open Sage 50 on your computer first |
| The client's folder on R: | e.g. `R:\Concetta Enterprises Inc\` |

### Step-by-Step

**Step 1 — Place the bank statement in the drop folder**

Copy or save the bank statement CSV file into the client's drop folder:

```
R:\[Client Folder Name]\drop\
```

For example, for Concetta:
```
R:\Concetta Enterprises Inc\drop\
```

The filename will be whatever the bank produces — that's fine, the program reads it automatically.

---

**Step 2 — Open PowerShell and activate (see Section 3)**

---

**Step 3 — Run the monthly close**

```powershell
python demo/monthly_close_demo.py --period 2026-05 --skip-hst
```

Replace `2026-05` with the actual year and month (always use `YYYY-MM` format).

> **The `--skip-hst` flag** tells the program to skip the HST return step.
> Remove it only if you have already run the tax summary export (see below).

To also have the program post journal entries directly into Sage 50, add
`--post-to-sage50` (Sage 50 must be open first):

```powershell
python demo/monthly_close_demo.py --period 2026-05 --skip-hst --post-to-sage50
```

---

**Step 4 — Read the results**

The program will print a summary like this:

```
  VTX-OS Monthly Close Summary
  ══════════════════════════════════════════════════════════════
  1.  Engagement letter indexed         OK
  2.  Bank statement processed          OK
  2b. Journal entries posted            OK  (posted 47, 3 to suspense)
  3.  GL reconciliation                 OK
  4.  HST return prepared               SKIPPED
  5.  RAG context retrieved             OK
  6.  Close email sent                  OK

  Bank: deposits $12,450.00  withdrawals $9,820.00  net +$2,630.00
  Session ID:  a1b2c3d4-...
  Elapsed:     8,241 ms
```

- **OK** next to each step means it completed successfully.
- **"to suspense"** means those transactions need your manual review in Sage 50.

---

**Step 5 — Review suspense items in Sage 50**

Any transaction posted to account **5900 (Suspense)** needs you to move it to
the correct account in Sage 50. These are usually:
- Cheque payments with no payee identified
- New vendors not yet in the rules
- Intercompany transfers

---

### Running the Tax Summary (for HST return)

Before running the HST step, you must first export the tax data from Sage 50.
Sage 50 must be open with the company file.

```powershell
python scripts/sage50_export.py --client concetta --period 2026-05 --skip-gl --skip-tb
```

This writes `tax-summary-2026-05.csv` to the drop folder. Then run the full close
without `--skip-hst`:

```powershell
python demo/monthly_close_demo.py --period 2026-05 --post-to-sage50
```

---

### Exporting All Three Sage 50 Reports at Once

If you need all reports (GL, Trial Balance, and Tax Summary) for a period:

```powershell
python scripts/sage50_export.py --client concetta --period 2026-05
```

This writes three files to `R:\Concetta Enterprises Inc\drop\`:

| File | Replaces this manual Sage 50 export |
|------|-------------------------------------|
| `gl-2026-05.csv` | Reports → General Journal → Export |
| `tb-2026-05.csv` | Reports → Financials → Trial Balance → Export |
| `tax-summary-2026-05.csv` | Reports → Tax → Tax Summary → Export |

> **Sage 50 must be open** with the company file loaded before running this command.

---

## 5. Year-End Worksheet

At the end of each fiscal year, generate the formatted Excel worksheet for
reviewing and entering adjusting entries.

**Concetta Enterprises** has a **April 30** fiscal year-end, so you run this
after completing April's bookkeeping.

### Step 1 — Export the Trial Balance from Sage 50

Sage 50 must be open. Run:

```powershell
python scripts/sage50_export.py --client concetta --period 2026-04 --skip-gl --skip-tax
```

This writes `tb-2026-04.csv` to the drop folder.

Alternatively, you can still export manually from Sage 50 (Reports → Financials →
Trial Balance → as at April 30, 2026 → Export CSV) and save it as:

```
R:\Concetta Enterprises Inc\drop\tb-2026-04.csv
```

### Step 2 — Generate the Excel Worksheet

```powershell
python scripts/year_end.py --client concetta --period 2026-04
```

The program will print something like:

```
Client : Concetta Enterprises Inc (concetta)
Period : 2026-04
TB CSV : R:\Concetta Enterprises Inc\drop\tb-2026-04.csv
Accounts : 47 posting accounts
TB Debit : 350,368.99   TB Credit : 350,368.99

Output : R:\Concetta Enterprises Inc\Year End\concetta_yearend_2026-04.xlsx
Done.
```

### Step 3 — Open the Worksheet

Navigate in File Explorer to:
```
R:\Concetta Enterprises Inc\Year End\
```
Open `concetta_yearend_2026-04.xlsx` in Excel.

The worksheet has four tabs:
- **0. Cover Sheet** — client name, year-end date, prepared by
- **1. Worksheet** — trial balance + columns for adjusting entries (you fill these in)
- **2. Adjusting Entries** — enter AJEs here; the Worksheet tab pulls them in automatically
- **3. Income Statement / 4. Balance Sheet** — formula-driven, update as you enter AJEs

> **Do not edit columns E through M** on the Worksheet tab — they contain
> formulas that calculate the adjusted totals automatically.

---

## 6. Adding a New Client

Follow these steps **in order** each time you take on a new client.

### What you need from the client

Before starting, collect:

| Information | Example |
|---|---|
| Legal business name (exactly as it appears on bank statements) | `Arrow Mechanical Services Inc` |
| Bank account number (from a bank statement) | `1234-5678901` |
| Bank name | TD, RBC, Scotiabank, CIBC, BMO, Desjardins |
| Client's email address (who sends the statements) | `owner@arrowmech.com` |
| Fiscal year-end month | `12` (December) |
| Sage 50 GL code for their bank account | `1060` (or ask Jorge) |

---

### Step 1 — Create the Client Folder on R:

1. Open File Explorer and navigate to `R:\`
2. Create a new folder with the client's exact legal name:
   ```
   R:\Arrow Mechanical Services Inc\
   ```
3. Inside that folder, create a subfolder called `drop`:
   ```
   R:\Arrow Mechanical Services Inc\drop\
   ```
4. Also create a `Year End` subfolder for year-end packages:
   ```
   R:\Arrow Mechanical Services Inc\Year End\
   ```

> **Folder name matters.** It must match exactly what you put in the registry
> (Step 2). No extra spaces, no abbreviations.

---

### Step 2 — Register the Client

Open the master client registry in Excel or Notepad:
```
R:\bookkeeping\client_accounts.csv
```

> **If this file opens in Excel:** do NOT let Excel change the format.
> Save it as CSV (comma separated), not as .xlsx.

Add a new row at the bottom with these columns:

| Column | What to enter | Example |
|--------|---------------|---------|
| `account_no` | Bank account number (dashes OK) | `1234-5678901` |
| `r_folder` | Exact folder name you created in Step 1 | `Arrow Mechanical Services Inc` |
| `client_id` | Short lowercase ID (no spaces, use hyphens) | `arrow-mechanical` |
| `gl_bank_account` | Sage 50 GL code for bank | `1060` |
| `bank` | Bank name | `TD` |
| `sender_email` | Client's email (optional, used for alerts) | `owner@arrowmech.com` |
| `year_end_month` | Number 1–12 for fiscal year-end | `12` |

**The file should look like this after adding the new row:**

```
account_no,r_folder,client_id,gl_bank_account,bank,sender_email,year_end_month
0000-1234567,Example Client Inc,example,1060,TD,owner@example.com,3
0000-9876543,Arrow Mechanical Services Inc,arrow-mechanical,1060,TD,owner@arrowmech.com,12
```

Save the file.

---

### Step 3 — Test the Registration

Open PowerShell (Section 3) and run:

```powershell
python scripts/sage50_export.py --client arrow-mechanical --period 2026-01 --dry-run
```

You should see:
```
=== sage50_export  client=arrow-mechanical  period=2026-01 ===
  drop dir : R:\Arrow Mechanical Services Inc\drop
  [dry-run] files will NOT be written
...
All exports completed successfully.
```

If you see `ERROR: client 'arrow-mechanical' not found`, go back and check
that the `client_id` in the CSV matches exactly what you typed in the command.

---

### Step 4 — Set Up Sage 50 (if not already done)

- Open Sage 50 and load the client's company file (`.SAI`)
- Confirm the chart of accounts and bank account numbers
- If the client is new to Sage 50, enter the opening balances as a General Journal entry

---

### Step 5 — First Month Run

Place the first bank statement CSV in:
```
R:\Arrow Mechanical Services Inc\drop\
```

Then run the monthly close (Sage 50 must be open):

```powershell
python demo/monthly_close_demo.py --period 2026-01 --skip-hst
```

> **First-month tip:** Expect many "suspense" items on the first run.
> The program learns from rules you configure — on the first month, most
> transactions won't match existing rules and will land in GL 5900 (Suspense).
> Review them in Sage 50 and move them to the correct accounts.
> Subsequent months will be much cleaner.

---

## 7. Troubleshooting

### "The system cannot find the path specified" or "R: drive not found"

The R: drive is not mapped. Ask IT to reconnect it. This usually happens after
a computer restart.

---

### "ERROR: client 'xyz' not found"

The client ID you typed doesn't match what's in `R:\bookkeeping\client_accounts.csv`.

1. Open the CSV file and look at the `client_id` column.
2. Make sure you typed it exactly (lowercase, hyphens, no spaces).

---

### "FileNotFoundError: Sage50Bridge.exe not found"

The Sage 50 bridge program needs to be compiled. Contact Jorge — this is a
one-time technical setup.

---

### "OpenDatabase: FAIL_MYSQL_NOTRUNNING"

Sage 50 is not open. Open Sage 50 with the client's company file loaded,
then try again.

---

### "OpenDatabase: FAIL_USER_LOGON_FAILED"

The Sage 50 username or password is wrong. In Sage 50:
- Go to **Setup → Manage Users**
- Make sure "Allow Third-Party Access" is checked for the user

---

### "Cannot open database: FAIL_CONNECTIONMGR_NONE"

Sage 50's Connection Manager is not running. Simply close and re-open Sage 50,
wait for it to fully load, then try your command again.

---

### The program ran but nothing was posted to Sage 50

Check that you included `--post-to-sage50` in your command. Without it, the
program runs in a dry-run mode for Sage 50 (it categorizes and reports but
does not write to Sage 50).

---

### "WARNING: TB is out of balance"

The trial balance credits and debits don't match. This means there may be
unposted entries in Sage 50. In Sage 50:
- Go to **Reports → Financials → Trial Balance**
- Check the totals match
- Look for unposted or pending journal entries

---

### Something else went wrong

Every program run produces an audit log in Google Cloud BigQuery. Contact Jorge
with the **Session ID** printed at the bottom of the program output — it uniquely
identifies the exact run and all its steps.

---

## 8. Quick Reference Card

Cut out or print this page and keep it at your desk.

```
╔══════════════════════════════════════════════════════════════════╗
║              VTX-OS QUICK REFERENCE                             ║
╠══════════════════════════════════════════════════════════════════╣
║  STEP 1 — Open PowerShell and activate every session            ║
║                                                                  ║
║  cd C:\Users\JorgeJr\vtx-os                                     ║
║  .venv\Scripts\activate                                          ║
║  (look for the (vtx-os) prefix before running anything)         ║
╠══════════════════════════════════════════════════════════════════╣
║  MONTHLY CLOSE  (replace 2026-05 with your period)              ║
║                                                                  ║
║  python demo/monthly_close_demo.py --period 2026-05 --skip-hst  ║
║                                                                  ║
║  With journal posting to Sage 50:                               ║
║  python demo/monthly_close_demo.py --period 2026-05 --skip-hst  ║
║                                   --post-to-sage50              ║
╠══════════════════════════════════════════════════════════════════╣
║  SAGE 50 EXPORTS  (Sage 50 must be open)                        ║
║                                                                  ║
║  All three at once:                                             ║
║  python scripts/sage50_export.py --client CLIENT --period PERIOD ║
║                                                                  ║
║  Trial balance only (for year-end):                             ║
║  python scripts/sage50_export.py --client CLIENT --period PERIOD ║
║                                   --skip-gl --skip-tax          ║
║                                                                  ║
║  Tax summary only (for HST):                                    ║
║  python scripts/sage50_export.py --client CLIENT --period PERIOD ║
║                                   --skip-gl --skip-tb           ║
╠══════════════════════════════════════════════════════════════════╣
║  YEAR-END WORKSHEET  (after trial balance is in drop folder)    ║
║                                                                  ║
║  python scripts/year_end.py --client CLIENT --period YYYY-MM    ║
║                                                                  ║
║  Output: R:\[Client Folder]\Year End\[client]_yearend_YYYY-MM.xlsx║
╠══════════════════════════════════════════════════════════════════╣
║  REGISTERED CLIENTS  (from R:\bookkeeping\client_accounts.csv)  ║
║                                                                  ║
║  Client ID           Folder                       Year-End      ║
║  ──────────────────  ─────────────────────────    ────────      ║
║  concetta            Concetta Enterprises Inc      April (04)   ║
║                                                                  ║
║  Add new clients: edit client_accounts.csv (see Section 6)      ║
╠══════════════════════════════════════════════════════════════════╣
║  COMMON ERRORS                                                   ║
║                                                                  ║
║  FAIL_MYSQL_NOTRUNNING  → Open Sage 50 first                    ║
║  client 'X' not found   → Check spelling in client_accounts.csv ║
║  R: drive not found     → Ask IT to reconnect the network drive  ║
╚══════════════════════════════════════════════════════════════════╝
```

---

### Client ID Reference

The `--client` flag in every command uses the short ID from the registry.
As you add more clients, update this table:

| Client Name | `--client` flag | Year-End |
|---|---|---|
| Concetta Enterprises Inc | `concetta` | April (`--period YYYY-04`) |
| Canadian Federation of theotherapy | `theotherapy` | December (`--period YYYY-12`) |
| *(new client)* | *(add here)* | |

> **theotherapy** has **two bank accounts** registered (BMO GL `1060` and `1065`) — both
> route to the same `theotherapy` client and share its categorization ruleset.

---

### File Locations Summary

| What | Where |
|---|---|
| Client registry | `R:\bookkeeping\client_accounts.csv` |
| Bank statements (in) | `R:\[Client Folder]\drop\` |
| Sage 50 exports (out) | `R:\[Client Folder]\drop\` |
| Year-end worksheets | `R:\[Client Folder]\Year End\` |
| Excel template | `R:\Templates\TEMPLATE_YearEnd_Accounting_Professional_BLANK_v2.xlsx` |
| Program folder | `C:\Users\JorgeJr\vtx-os\` |

---

*Questions? Contact Jorge Quinonez CPA — jquinonez2980@gmail.com*
