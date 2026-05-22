"""
demo/monthly_close_demo.py
Monthly Close Demo -- full 6-agent pipeline via A2A.

Steps:
  1  INDEX_DOCUMENT      RagAgent indexes the engagement letter
  2  BOOKKEEPING_RUN     BookkeepingAgent parses the bank statement
  3  RECONCILE_GL        ReconcileGLAgent matches bank vs Sage 50 GL
  4  PREPARE_HST_RETURN  PrepareHSTReturnAgent calculates GST34 lines
  5  RAG_QUERY           RagAgent retrieves engagement letter context
  6  SEND_CLIENT_EMAIL   GmailCommsAgent drafts and sends the close email

All 6 steps route through OrchestratorAgent -> A2ATransport -> sub-agent.
A shared session_id links every audit record for this monthly close run.

Usage (live, requires ADC + configured secrets):
    python demo/monthly_close_demo.py

    Optional env overrides:
        CLIENT_ID       default "concetta-enterprises"
        PERIOD          default "2025-12"
        CLIENT_EMAIL    default from engagement letter
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal
from pathlib import Path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_pipeline(
    client_id:          str,
    period:             str,                # "YYYY-MM"
    bank_csv_path:      str,                # TD-format bank statement CSV
    tax_csv_path:       str,                # Sage 50 Tax Summary CSV
    engagement_letter:  str,                # full engagement letter text
    client_email:       str,                # recipient of the close email
    account_no:         str  = "xxxx1234",
    gl_csv_path:        str | None = None,  # Sage 50 GL export CSV (auto-fetched after posting when None)
    business_no:        str | None = None,
    session_id:         str | None = None,
    verbose:            bool = True,
    dry_run_email:      bool = False,       # skip Gmail; write email body to email_output_path
    email_output_path:  str | None = None,  # where to save email body when dry_run_email=True
    post_to_sage50:     bool = True,        # False = dry-run journal entries (build but don't write to Sage 50)
    sage50_sai:         str | None = None,  # Sage 50 .SAI path (falls back to Secret Manager)
    sage50_user:        str | None = None,
    sage50_password:    str | None = None,
) -> dict:
    """Run the full monthly close pipeline.  Returns a dict with all results.

    Keys: index, bookkeeping, recon, hst, rag, email, session_id, elapsed_ms
    """
    from agents.base import TaskRequest, TaskResult, TaskType
    from agents.orchestrator import OrchestratorAgent
    from models.base import EventStatus

    session_id = session_id or str(uuid.uuid4())
    orch = OrchestratorAgent()
    t0 = time.monotonic()
    results: dict = {"session_id": session_id}

    def _run(step_num, label: str, task_type: TaskType, payload: dict):
        if verbose:
            print(f"  [{step_num}/6] {label}...", end=" ", flush=True)
        req = TaskRequest(
            task_type=task_type,
            payload=payload,
            session_id=session_id,
            requested_by="monthly-close-demo",
        )
        r = orch.run(req)
        if verbose:
            status = "OK" if r.ok else f"FAILED: {r.error}"
            print(status)
        return r

    # ------------------------------------------------------------------
    # Step 1 — Index engagement letter into RAG store
    # ------------------------------------------------------------------
    results["index"] = _run(1, "Indexing engagement letter", TaskType.INDEX_DOCUMENT, {
        "document_type":  "engagement_letter",
        "client_id":      client_id,
        "source_text":    engagement_letter,
        "fiscal_year":    int(period.split("-")[0]),
        "fiscal_period":  period,
        "chunk_size":     800,
        "chunk_overlap":  80,
    })

    # ------------------------------------------------------------------
    # Step 2 — Bookkeeping: parse and categorize bank statement
    # ------------------------------------------------------------------
    results["bookkeeping"] = _run(2, "Bookkeeping run", TaskType.BOOKKEEPING_RUN, {
        "csv_path":        bank_csv_path,
        "account_no":      account_no,
        "gl_bank_account": "1060",
        "period":          period,
        "client_id":       client_id,  # activates client-specific ruleset (e.g. ConcettaRuleset)
        "queue_reviews":   True,
        "notify_chat":     False,
    })

    # ------------------------------------------------------------------
    # Step 2b — Post journal entries to Sage 50
    # Always runs; post_to_sage50=False builds entries without writing (dry-run).
    # ------------------------------------------------------------------
    results["journal"] = _run(
        "2b", "Posting journal entries to Sage 50",
        TaskType.POST_JOURNAL_ENTRIES, {
            "bank_csv_path":   bank_csv_path,
            "period":          period,
            "gl_bank_account": "1060",
            "client_id":       client_id,
            "account_no":      account_no,
            "sai_file":        sage50_sai,
            "sage50_user":     sage50_user,
            "sage50_password": sage50_password,
            "dry_run":         not post_to_sage50,
        })

    # ------------------------------------------------------------------
    # Step 2c — Re-fetch GL from Sage 50 after journal posting
    # ------------------------------------------------------------------
    if results["journal"].ok and post_to_sage50:
        if verbose:
            print(f"  [2c/6] Re-fetching GL from Sage 50...", end=" ", flush=True)
        try:
            from sage50.bridge_reader import fetch_gl_csv
            from sage50.categorization_rules import CONCETTA_ACCOUNT_MAP
            _gl_dest = Path(bank_csv_path).parent / f"gl-{period}.csv"
            _n = fetch_gl_csv(
                period, _gl_dest,
                account_map=CONCETTA_ACCOUNT_MAP,
                load_bq=True,
                sai_file=sage50_sai,
                user=sage50_user,
                password=sage50_password,
            )
            gl_csv_path = str(_gl_dest)
            if verbose:
                print(f"{_n} rows → {_gl_dest.name}")
        except Exception as exc:
            if verbose:
                print(f"FAILED: {exc}")

    # ------------------------------------------------------------------
    # Step 3 — GL reconciliation: bank vs Sage 50 GL
    # ------------------------------------------------------------------
    results["recon"] = _run(3, "GL reconciliation", TaskType.RECONCILE_GL, {
        "gl_csv_path":   gl_csv_path,
        "bank_csv_path": bank_csv_path,   # avoids querying BQ for bank data
        "account_no":    account_no,
        "period":        period,
    })

    # ------------------------------------------------------------------
    # Step 4 — HST return preparation
    # ------------------------------------------------------------------
    results["hst"] = _run(4, "Preparing HST return", TaskType.PREPARE_HST_RETURN, {
        "tax_csv_path":  tax_csv_path,
        "return_period": period,
        "business_no":   business_no,
    })

    # ------------------------------------------------------------------
    # Step 5 — RAG query: retrieve engagement letter context for email
    # ------------------------------------------------------------------
    results["rag"] = _run(5, "RAG context query", TaskType.RAG_QUERY, {
        "query":         f"What are the reporting requirements and fee schedule for {client_id}?",
        "client_id":     client_id,
        "document_type": "engagement_letter",
        "top_k":         3,
    })

    # ------------------------------------------------------------------
    # Step 6 — Draft and send monthly close email
    # ------------------------------------------------------------------
    bk_out   = results["bookkeeping"].output  if results["bookkeeping"].ok  else {}
    recon_out = results["recon"].output       if results["recon"].ok        else {}
    hst_out  = results["hst"].output         if results["hst"].ok          else {}
    rag_ctx  = results["rag"].output.get("context", "") if results["rag"].ok else ""

    email_payload = _compose_email(
        client_id=client_id,
        period=period,
        bk=bk_out,
        recon=recon_out,
        hst=hst_out,
        rag_context=rag_ctx,
        to=client_email,
    )
    if dry_run_email:
        if verbose:
            print(f"  [6/6] Dry-run email (not sent via Gmail)...", end=" ", flush=True)
        saved_to = ""
        if email_output_path:
            Path(email_output_path).write_text(
                f"To: {email_payload['to']}\n"
                f"Subject: {email_payload['subject']}\n\n"
                f"{email_payload['body']}",
                encoding="utf-8",
            )
            saved_to = email_output_path
        if verbose:
            print(f"saved to {saved_to}" if saved_to else "OK (body in output)")
        results["email"] = TaskResult(
            task_id=str(uuid.uuid4()),
            task_type=TaskType.SEND_CLIENT_EMAIL,
            agent_id="gmail-comms-agent",
            status=EventStatus.SUCCESS,
            output={
                "message_id": "(dry-run)",
                "thread_id":  "(dry-run)",
                "to":         email_payload["to"],
                "subject":    email_payload["subject"],
                "body":       email_payload["body"],
                "saved_to":   saved_to,
            },
        )
    else:
        results["email"] = _run(6, "Sending close email", TaskType.SEND_CLIENT_EMAIL, email_payload)

    results["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
    return results


# ---------------------------------------------------------------------------
# Email composition
# ---------------------------------------------------------------------------

def _compose_email(
    client_id: str,
    period: str,
    bk: dict,
    recon: dict,
    hst: dict,
    rag_context: str,
    to: str,
) -> dict:
    """Build SEND_CLIENT_EMAIL payload from pipeline outputs."""

    def _dec(key, d):
        return Decimal(str(d.get(key, "0")))

    deposits     = _dec("total_deposits",    bk)
    withdrawals  = _dec("total_withdrawals", bk)
    net          = _dec("net_movement",      bk)
    total_txn    = bk.get("total_transactions", 0)
    auto_cat     = bk.get("auto_categorized", 0)
    needs_rev    = bk.get("needs_review", 0)

    matched      = recon.get("matched_count",       0)
    bank_count   = recon.get("bank_txn_count",      0)
    unmat_bank   = recon.get("unmatched_bank_count", 0)
    unmat_gl     = recon.get("unmatched_gl_count",   0)
    recon_status = "RECONCILED" if recon.get("is_reconciled") else "DIFFERENCES NOTED"

    line_101 = _dec("line_101_total_revenue", hst)
    line_103 = _dec("line_103_hst_collected", hst)
    line_106 = _dec("line_106_itc_claimed",   hst)
    line_109 = _dec("line_109_net_tax",       hst)
    due_date = hst.get("filing_due_date", "N/A")

    # Trim RAG context for the email footer
    context_note = ""
    if rag_context:
        snippet = rag_context[:400].replace("\n", " ").strip()
        context_note = f"\n\nEngagement notes: {snippet}"

    body = (
        f"Dear Client,\n\n"
        f"Please find your {period} monthly close summary below.\n\n"
        f"BANK ACTIVITY\n"
        f"  Deposits:           ${deposits:>12,.2f}\n"
        f"  Withdrawals:        ${withdrawals:>12,.2f}\n"
        f"  Net Movement:       ${net:>+12,.2f}\n"
        f"  Transactions:       {total_txn:>4}  "
        f"({auto_cat} auto-categorized, {needs_rev} awaiting review)\n\n"
        f"GL RECONCILIATION -- {recon_status}\n"
        f"  Matched:            {matched:>4} of {bank_count} bank transactions\n"
        f"  Unmatched (bank):   {unmat_bank:>4}\n"
        f"  Unmatched (GL):     {unmat_gl:>4}\n\n"
        f"HST/GST RETURN -- Ontario 13% (filing due {due_date})\n"
        f"  Line 101  Total Revenue:   ${line_101:>12,.2f}\n"
        f"  Line 103  HST Collected:   ${line_103:>12,.2f}\n"
        f"  Line 106  ITCs Claimed:    ${line_106:>12,.2f}\n"
        f"  Line 109  Net Tax Owing:   ${line_109:>12,.2f}\n"
        f"{context_note}\n\n"
        f"{needs_rev} transaction(s) require your approval in the portal.\n\n"
        f"Regards,\nVTX Accounting OS"
    )

    client_name = client_id.replace("-", " ").title()
    return {
        "to":      to,
        "subject": f"{client_name} -- {period} Monthly Close",
        "body":    body,
    }


# ---------------------------------------------------------------------------
# CLI entry point (live run, requires ADC)
# ---------------------------------------------------------------------------

def _print_summary(results: dict) -> None:
    print("\n" + "=" * 62)
    print("  VTX-OS Monthly Close Summary")
    print("=" * 62)

    steps = [
        ("index",       "1.  Engagement letter indexed"),
        ("bookkeeping", "2.  Bank statement processed"),
        ("journal",     "2b. Journal entries posted"),
        ("recon",       "3.  GL reconciliation"),
        ("hst",         "4.  HST return prepared"),
        ("rag",         "5.  RAG context retrieved"),
        ("email",       "6.  Close email sent"),
    ]

    for key, label in steps:
        r = results.get(key)
        if r is None:
            print(f"  {label:<35} SKIPPED")
            continue
        status = "OK" if r.ok else f"FAILED ({r.error})"
        print(f"  {label:<35} {status}")

    jnl = results.get("journal")
    if jnl and jnl.ok:
        o = jnl.output
        print(f"\n  Journal: {o.get('posted',0)} posted  "
              f"{o.get('posted_to_suspense',0)} to suspense (GL 5900)  "
              f"{o.get('errors',0)} errors")

    bk = results.get("bookkeeping")
    if bk and bk.ok:
        o = bk.output
        deposits    = Decimal(str(o.get("total_deposits",    "0")))
        withdrawals = Decimal(str(o.get("total_withdrawals", "0")))
        net         = Decimal(str(o.get("net_movement",      "0")))
        print(f"\n  Bank: deposits ${deposits:,.2f}  withdrawals ${withdrawals:,.2f}  net ${net:+,.2f}")

    hst = results.get("hst")
    if hst and hst.ok:
        o = hst.output
        line_109 = Decimal(str(o.get("line_109_net_tax", "0")))
        due      = o.get("filing_due_date", "")
        print(f"  HST:  net tax owing ${line_109:,.2f}  (due {due})")

    print(f"\n  Session ID:  {results['session_id']}")
    print(f"  Elapsed:     {results.get('elapsed_ms', 0)} ms")
    print("=" * 62 + "\n")


if __name__ == "__main__":
    import argparse, os, sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    _parser = argparse.ArgumentParser(description="VTX-OS monthly close demo")
    _parser.add_argument("--post-to-sage50", action="store_true",
                         help="Post journal entries back into Sage 50 via the bridge")
    _parser.add_argument("--sage50-sai",      default=None, help="Sage 50 .SAI file path")
    _parser.add_argument("--sage50-user",     default=None)
    _parser.add_argument("--sage50-password", default=None)
    _args = _parser.parse_args()

    CLIENT_ID    = os.environ.get("CLIENT_ID",    "concetta-enterprises")
    PERIOD       = os.environ.get("PERIOD",       "2025-12")
    CLIENT_EMAIL = os.environ.get("CLIENT_EMAIL", "jquinonez2980@gmail.com")
    BASE         = Path(__file__).resolve().parents[1]

    ENGAGEMENT_LETTER = """
ENGAGEMENT LETTER -- Concetta Enterprises Inc.
Business Number: 123456789RT0001
Province: Ontario (HST 13%)
Fiscal Year End: December 31
Services: Monthly bookkeeping, HST/GST filing, annual T2 corporate tax return
Fee Schedule: $450/month bookkeeping, $200/HST return, $1,200/T2 return
Reporting: Monthly close package delivered by 15th of following month
Approval Portal: Client approval required for transactions flagged needs_review
Contact: Jorge Quinonez CPA, jquinonez2980@gmail.com
"""

    # Check whether Gmail OAuth credentials are configured
    _dry_run = False
    try:
        from core.secrets import get as _get_secret
        _get_secret("vtx-gmail-oauth-credentials")
    except ValueError:
        _dry_run = True
        print("  Gmail OAuth not configured — email will be saved locally.")
        print("  Run:  python scripts/gmail_auth.py  to set up Gmail OAuth.\n")

    _email_out = str(BASE / f"data/test-client/close-email-{PERIOD}.txt") if _dry_run else None

    print(f"\nRunning monthly close: {CLIENT_ID} | {PERIOD}\n")

    results = run_pipeline(
        client_id         = CLIENT_ID,
        period            = PERIOD,
        bank_csv_path     = str(BASE / "data/test-client/dec-2025-bank-extracted.csv"),
        gl_csv_path       = str(BASE / "data/test-client/concetta-dec2025-gl.csv"),
        tax_csv_path      = str(BASE / "data/test-client/concetta-dec2025-tax.csv"),
        engagement_letter = ENGAGEMENT_LETTER,
        client_email      = CLIENT_EMAIL,
        account_no        = "xxxx5443",
        business_no       = "123456789RT0001",
        verbose           = True,
        dry_run_email     = _dry_run,
        email_output_path = _email_out,
        post_to_sage50    = _args.post_to_sage50,
        sage50_sai        = _args.sage50_sai,
        sage50_user       = _args.sage50_user,
        sage50_password   = _args.sage50_password,
    )

    _print_summary(results)
    if _dry_run and _email_out:
        print(f"  Close email saved to: {_email_out}")
