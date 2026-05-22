"""
tests/p2_7_live.py
P2.7 LIVE end-to-end test — Concetta Enterprises Inc. Dec 2025.

Requires ADC configured (jquinonez2980@gmail.com).
Writes to LIVE BQ: vtx_accounting, vtx_audit, vtx_rag.

Gmail:
  If vtx-gmail-oauth-credentials is configured, email is sent live.
  Otherwise the pipeline runs with dry_run_email=True and the close
  email is written to data/test-client/close-email-2025-12.txt.

Expected results (Concetta Dec 2025):
  Bookkeeping:  20 transactions | deposits $23,249.07 | withdrawals $9,819.46
                all 20 need review (cheques + PC Mastercard not in default ruleset)
  GL Recon:     19 of 20 bank transactions matched
  HST Return:   Line 103  $5,850.00 | Line 109  $5,588.79 | due 2026-01-31
  Email:        sent live OR written to data/test-client/close-email-2025-12.txt

Checks:
   --- Pipeline structure ---
   1    All 6 result keys present
   2    session_id is non-empty string
   3    elapsed_ms > 0

   --- INDEX_DOCUMENT ---
   4    result.ok
   5    chunks_indexed >= 1

   --- BOOKKEEPING_RUN ---
   6    result.ok
   7    total_transactions == 20
   8    total_deposits == $23,249.07
   9    total_withdrawals == $9,819.46
  10    auto_categorized + needs_review == total_transactions
  11    all transactions flagged needs_review (cheques / PC Mastercard)
  12    net_movement == $13,429.61

   --- RECONCILE_GL ---
  13    result.ok
  14    bank_txn_count == 20
  15    matched_count == 19

   --- PREPARE_HST_RETURN ---
  16    result.ok
  17    line_103_hst_collected == $5,850.00
  18    line_109_net_tax == $5,588.79
  19    filing_due_date == 2026-01-31
  20    is_refund == False

   --- RAG_QUERY ---
  21    result.ok
  22    VECTOR_SEARCH executed without error

   --- SEND_CLIENT_EMAIL ---
  23    result.ok (live send OR dry-run)
  24    email subject contains "Concetta Enterprises" and "2025-12"
  25    email body contains HST net tax "5,588"
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE   = Path(__file__).resolve().parents[1]
PERIOD = "2025-12"

ENGAGEMENT_LETTER = """\
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


def _dec(v) -> Decimal:
    return Decimal(str(v))


def run() -> None:
    # Check Gmail credential availability
    dry_run_email = False
    try:
        from core.secrets import get as _get_secret
        _get_secret("vtx-gmail-oauth-credentials")
        print("  Gmail credentials: CONFIGURED (email will be sent live)")
    except ValueError:
        dry_run_email = True
        print("  Gmail credentials: NOT CONFIGURED (email will be saved to file)")

    email_out = str(BASE / f"data/test-client/close-email-{PERIOD}.txt") if dry_run_email else None

    from demo.monthly_close_demo import run_pipeline

    print(f"\nRunning P2.7 live pipeline: concetta-enterprises | {PERIOD}\n")

    results = run_pipeline(
        client_id         = "concetta-enterprises",
        period            = PERIOD,
        bank_csv_path     = str(BASE / "data/test-client/dec-2025-bank-extracted.csv"),
        gl_csv_path       = str(BASE / "data/test-client/concetta-dec2025-gl.csv"),
        tax_csv_path      = str(BASE / "data/test-client/concetta-dec2025-tax.csv"),
        engagement_letter = ENGAGEMENT_LETTER,
        client_email      = "jquinonez2980@gmail.com",
        account_no        = "xxxx5443",
        business_no       = "123456789RT0001",
        verbose           = True,
        dry_run_email     = dry_run_email,
        email_output_path = email_out,
        post_to_sage50    = False,
    )

    print()

    checks: list[tuple[str, bool]] = []

    # -------------------------------------------------------------- #
    # 1-3  Pipeline structure                                         #
    # -------------------------------------------------------------- #
    all_keys = {"index", "bookkeeping", "recon", "hst", "rag", "email"}
    checks.append(("All 6 result keys present",   all_keys.issubset(results.keys())))
    checks.append(("session_id is non-empty",      bool(results.get("session_id"))))
    checks.append(("elapsed_ms > 0",               int(results.get("elapsed_ms", 0)) > 0))

    # -------------------------------------------------------------- #
    # 4-5  INDEX_DOCUMENT                                             #
    # -------------------------------------------------------------- #
    idx = results.get("index")
    idx_out = idx.output if (idx and idx.ok) else {}
    checks.append(("INDEX_DOCUMENT result.ok",      idx is not None and idx.ok))
    checks.append(("chunks_indexed >= 1",
                   int(idx_out.get("chunks_indexed", 0)) >= 1))

    # -------------------------------------------------------------- #
    # 6-12  BOOKKEEPING_RUN                                           #
    # -------------------------------------------------------------- #
    bk = results.get("bookkeeping")
    bk_out = bk.output if (bk and bk.ok) else {}

    total_txn  = bk_out.get("total_transactions", 0)
    auto_cat   = bk_out.get("auto_categorized", 0)
    needs_rev  = bk_out.get("needs_review", 0)
    deposits   = _dec(bk_out.get("total_deposits",    "0"))
    withdrawals= _dec(bk_out.get("total_withdrawals", "0"))
    net        = _dec(bk_out.get("net_movement",      "0"))

    checks.append(("BOOKKEEPING_RUN result.ok",     bk is not None and bk.ok))
    checks.append(("total_transactions == 20",       total_txn == 20))
    checks.append(("total_deposits == $23,249.07",   deposits == _dec("23249.07")))
    checks.append(("total_withdrawals == $9,819.46", withdrawals == _dec("9819.46")))
    checks.append(("auto + needs_review == total",   auto_cat + needs_rev == total_txn))
    checks.append(("ConcettaRuleset: 15 auto-categorized, 5 needs review",
                   auto_cat == 15 and needs_rev == 5))
    checks.append(("net_movement == $13,429.61",     net == _dec("13429.61")))

    # -------------------------------------------------------------- #
    # 13-15  RECONCILE_GL                                             #
    # -------------------------------------------------------------- #
    recon = results.get("recon")
    recon_out = recon.output if (recon and recon.ok) else {}

    checks.append(("RECONCILE_GL result.ok",         recon is not None and recon.ok))
    checks.append(("bank_txn_count == 20",
                   recon_out.get("bank_txn_count", 0) == 20))
    checks.append(("matched_count == 19",
                   recon_out.get("matched_count",   0) == 19))

    # -------------------------------------------------------------- #
    # 16-20  PREPARE_HST_RETURN                                       #
    # -------------------------------------------------------------- #
    hst = results.get("hst")
    hst_out = hst.output if (hst and hst.ok) else {}

    line_103 = _dec(hst_out.get("line_103_hst_collected", "0"))
    line_109 = _dec(hst_out.get("line_109_net_tax",       "0"))
    due_date  = str(hst_out.get("filing_due_date", ""))
    is_refund = hst_out.get("is_refund", True)

    checks.append(("PREPARE_HST_RETURN result.ok",       hst is not None and hst.ok))
    checks.append(("line_103_hst_collected == $5,850.00", line_103 == _dec("5850.00")))
    checks.append(("line_109_net_tax == $5,588.79",       line_109 == _dec("5588.79")))
    checks.append(("filing_due_date == 2026-01-31",       due_date == "2026-01-31"))
    checks.append(("is_refund == False",                  is_refund is False))

    # -------------------------------------------------------------- #
    # 21-22  RAG_QUERY                                                #
    # -------------------------------------------------------------- #
    rag = results.get("rag")
    checks.append(("RAG_QUERY result.ok",                rag is not None and rag.ok))
    checks.append(("VECTOR_SEARCH ran without error",
                   rag is not None and (rag.ok or "VECTOR_SEARCH" not in (rag.error or ""))))

    # -------------------------------------------------------------- #
    # 23-25  SEND_CLIENT_EMAIL                                        #
    # -------------------------------------------------------------- #
    email_r = results.get("email")
    email_out_data = email_r.output if (email_r and email_r.ok) else {}

    checks.append(("SEND_CLIENT_EMAIL result.ok (live or dry-run)", email_r is not None and email_r.ok))

    subject = email_out_data.get("subject", "")
    checks.append(("Subject contains 'Concetta Enterprises' and '2025-12'",
                   "Concetta Enterprises" in subject and "2025-12" in subject))

    # Email body: included in output["body"] for both live and dry-run paths
    body = email_out_data.get("body", "")
    checks.append(("Email body contains HST net tax '5,588'", "5,588" in body))

    # -------------------------------------------------------------- #
    # Report                                                          #
    # -------------------------------------------------------------- #
    passed = sum(1 for _, ok in checks if ok)
    total  = len(checks)
    print(f"P2.7 Live test -- {passed}/{total} checks passed\n")
    for i, (label, ok) in enumerate(checks, 1):
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {i:2d}  {label}")

    if email_out and Path(email_out).exists():
        print(f"\n  Close email saved to: {email_out}")

    if passed < total:
        print(f"\n{total - passed} check(s) FAILED.")
        sys.exit(1)
    else:
        print("\nAll checks passed.")


if __name__ == "__main__":
    run()
