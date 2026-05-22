"""
tests/p2_6_monthly_close_smoke.py
P2.6 smoke test - monthly close demo pipeline.

OFFLINE: mock BQ, mock Vertex AI embeddings, mock Gmail API.
         Synthetic CSV files written to a temp directory.

Checks:
   --- Registration ---
   1    PREPARE_HST_RETURN registered in OrchestratorAgent
   2    INDEX_DOCUMENT registered in OrchestratorAgent
   3    SEND_CLIENT_EMAIL registered in OrchestratorAgent

   --- Full pipeline (all 6 steps) ---
   4    Step 1 (INDEX_DOCUMENT):     result.ok
   5    Step 2 (BOOKKEEPING_RUN):    result.ok
   6    Step 3 (RECONCILE_GL):       result.ok
   7    Step 4 (PREPARE_HST_RETURN): result.ok
   8    Step 5 (RAG_QUERY):          result.ok
   9    Step 6 (SEND_CLIENT_EMAIL):  result.ok

   --- Bookkeeping output ---
  10    total_transactions == 4
  11    total_deposits > 0
  12    auto_categorized + needs_review == total_transactions
  13    net_movement > 0 (deposits exceed withdrawals)

   --- GL reconciliation output ---
  14    matched_count > 0
  15    bank_txn_count == 4
  16    is_reconciled is a bool

   --- HST return output ---
  17    line_103_hst_collected > 0
  18    line_109_net_tax > 0
  19    filing_due_date == "2026-01-31"

   --- RAG output ---
  20    chunks_indexed > 0 (INDEX_DOCUMENT step)
  21    RAG_QUERY context is non-empty

   --- Email output ---
  22    Gmail send() was called exactly once
  23    email subject contains the period "2025-12"
  24    sent email body contains the HST net-tax amount (5,080)

   --- Pipeline integrity ---
  25    results dict contains all 6 step keys
  26    session_id is a non-empty string
  27    elapsed_ms > 0
"""

from __future__ import annotations

import base64
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# Synthetic CSV data
# ---------------------------------------------------------------------------

BANK_CSV = """\
Date,Description,Withdrawals ($),Deposits ($),Balance ($)
12/05/2025,ACME CORP PAYMENT,,3500.00,13500.00
12/10/2025,NORTHVIEW CONSULTING,,2000.00,15500.00
12/15/2025,BELL CANADA INTERNET,120.00,,15380.00
12/20/2025,RECEIVER GENERAL HST,250.00,,15130.00
"""

GL_CSV = """\
Date,Source No.,Account No.,Account Description,Debit,Credit,Description
12/05/2025,J-001,1060,TD Bank Chequing,3500.00,,ACME CORP PAYMENT
12/10/2025,J-002,1060,TD Bank Chequing,2000.00,,NORTHVIEW CONSULTING
12/15/2025,J-003,1060,TD Bank Chequing,,120.00,BELL CANADA INTERNET
12/20/2025,J-004,1060,TD Bank Chequing,,250.00,RECEIVER GENERAL HST
12/05/2025,J-001,4000,Revenue,,3500.00,ACME CORP PAYMENT
12/10/2025,J-002,4000,Revenue,,2000.00,NORTHVIEW CONSULTING
12/15/2025,J-003,5500,Utilities,120.00,,BELL CANADA INTERNET
12/20/2025,J-004,2100,HST Payable,,250.00,RECEIVER GENERAL HST
"""

# Ontario HST 13%, Dec 2025
TAX_CSV = """\
Period Start,Period End,Tax Code,Description,Taxable Sales,Tax Collected,Taxable Purchases,Input Tax Credits,Net Tax
12/01/2025,12/31/2025,H,Ontario HST 13%,40000.00,5200.00,923.08,120.00,5080.00
"""

ENGAGEMENT_LETTER = """\
ENGAGEMENT LETTER -- Northview Consulting Inc.
Business Number: 987654321RT0001
Province: Ontario (HST 13%)
Services: Monthly bookkeeping, HST/GST filing, T2 corporate return
Fee Schedule: $400/month bookkeeping, $200/HST return
Reporting: Monthly close package delivered by 15th of following month
Contact: Jorge Quinonez CPA, jquinonez2980@gmail.com
"""


# ---------------------------------------------------------------------------
# MockBQClient
# ---------------------------------------------------------------------------

class MockBQClient:
    def __init__(self):
        self.inserted: dict[str, list] = {}
        self.queries:  list[str]       = []
        self.datasets_created: list    = []

    def get_table(self, table_id):
        from google.cloud.exceptions import NotFound
        raise NotFound(f"(mock) {table_id}")

    def create_table(self, table):
        return table

    def create_dataset(self, dataset, **_):
        self.datasets_created.append(dataset)
        return dataset

    def insert_rows_json(self, table_id, rows, **_):
        self.inserted.setdefault(str(table_id), []).extend(rows)
        return []

    def query(self, sql, **_):
        self.queries.append(sql)
        job = MagicMock()
        if "VECTOR_SEARCH" in sql:
            row = _MockRow(
                chunk_id="chunk-smoke-001",
                client_id="northview-consulting",
                document_type="engagement_letter",
                chunk_text="Fee Schedule: $400/month bookkeeping, $200/HST return.",
                fiscal_year=2025,
                fiscal_period="2025-12",
                source_uri=None,
                distance=0.08,
            )
            job.result.return_value = [row]
        else:
            job.result.return_value = []
        return job

    def total_rows(self) -> int:
        return sum(len(v) for v in self.inserted.values())


class _MockRow:
    def __init__(self, **data):
        self._data = data

    def keys(self):
        return self._data.keys()

    def __getitem__(self, k):
        return self._data[k]


def _inject(client):
    import core.bq_loader, core.audit, core.approval_queue
    core.bq_loader._client         = client
    core.audit._client             = client
    core.approval_queue._bq_client = client


# ---------------------------------------------------------------------------
# Vertex AI embedding mock
# ---------------------------------------------------------------------------

FAKE_VECTOR = [0.1, 0.2, 0.3]


def _make_embed_mock():
    mock_model = MagicMock()
    mock_model.get_embeddings.side_effect = lambda batch: [
        type("E", (), {"values": FAKE_VECTOR})() for _ in batch
    ]
    return mock_model


# ---------------------------------------------------------------------------
# Gmail service mock
# ---------------------------------------------------------------------------

def _make_gmail_mock():
    svc = MagicMock()
    svc.users.return_value.messages.return_value.send.return_value.execute.return_value = {
        "id": "msg-smoke-001",
        "threadId": "thr-smoke-001",
    }
    return svc


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run() -> None:
    mock_bq    = MockBQClient()
    _inject(mock_bq)

    from agents.base import TaskType
    from agents.orchestrator import OrchestratorAgent

    checks: list[tuple[str, bool]] = []

    # ------------------------------------------------------------------ #
    # 1-3  Registration                                                    #
    # ------------------------------------------------------------------ #
    reg = OrchestratorAgent.registered_types()
    checks.append(("PREPARE_HST_RETURN registered",  TaskType.PREPARE_HST_RETURN in reg))
    checks.append(("INDEX_DOCUMENT registered",       TaskType.INDEX_DOCUMENT     in reg))
    checks.append(("SEND_CLIENT_EMAIL registered",    TaskType.SEND_CLIENT_EMAIL  in reg))

    # ------------------------------------------------------------------ #
    # Write synthetic CSV files to a temp directory                        #
    # ------------------------------------------------------------------ #
    with tempfile.TemporaryDirectory() as tmpdir:
        bank_path = Path(tmpdir) / "bank.csv"
        gl_path   = Path(tmpdir) / "gl.csv"
        tax_path  = Path(tmpdir) / "tax.csv"
        bank_path.write_text(BANK_CSV, encoding="utf-8")
        gl_path.write_text(GL_CSV,     encoding="utf-8")
        tax_path.write_text(TAX_CSV,   encoding="utf-8")

        embed_mock  = _make_embed_mock()
        gmail_mock  = _make_gmail_mock()

        from demo.monthly_close_demo import run_pipeline

        with patch("vertexai.init"), \
             patch("vertexai.language_models.TextEmbeddingModel.from_pretrained",
                   return_value=embed_mock), \
             patch("agents.gmail_comms._load_creds_json",
                   return_value='{"client_id":"x","client_secret":"x","refresh_token":"x","token_uri":"https://oauth2.googleapis.com/token"}'), \
             patch("agents.gmail_comms._build_service", return_value=gmail_mock):

            results = run_pipeline(
                client_id         = "northview-consulting",
                period            = "2025-12",
                bank_csv_path     = str(bank_path),
                gl_csv_path       = str(gl_path),
                tax_csv_path      = str(tax_path),
                engagement_letter = ENGAGEMENT_LETTER,
                client_email      = "client@northview.ca",
                account_no        = "xxxx9999",
                business_no       = "987654321RT0001",
                verbose           = False,
                post_to_sage50    = False,
            )

    # ------------------------------------------------------------------ #
    # 4-9  All 6 pipeline steps succeed                                   #
    # ------------------------------------------------------------------ #
    for step_num, key, label in [
        (4, "index",       "Step 1 INDEX_DOCUMENT result.ok"),
        (5, "bookkeeping", "Step 2 BOOKKEEPING_RUN result.ok"),
        (6, "recon",       "Step 3 RECONCILE_GL result.ok"),
        (7, "hst",         "Step 4 PREPARE_HST_RETURN result.ok"),
        (8, "rag",         "Step 5 RAG_QUERY result.ok"),
        (9, "email",       "Step 6 SEND_CLIENT_EMAIL result.ok"),
    ]:
        r = results.get(key)
        checks.append((label, r is not None and r.ok))

    # ------------------------------------------------------------------ #
    # 10-13  Bookkeeping output                                           #
    # ------------------------------------------------------------------ #
    bk = results.get("bookkeeping")
    bk_out = bk.output if (bk and bk.ok) else {}

    total_txn   = bk_out.get("total_transactions", 0)
    auto_cat    = bk_out.get("auto_categorized", 0)
    needs_rev   = bk_out.get("needs_review", 0)
    deposits    = Decimal(str(bk_out.get("total_deposits",    "0")))
    net         = Decimal(str(bk_out.get("net_movement",      "0")))

    checks.append(("Bookkeeping total_transactions == 4", total_txn == 4))
    checks.append(("Bookkeeping total_deposits > 0",      deposits > 0))
    checks.append(("auto_categorized + needs_review == total_transactions",
                   auto_cat + needs_rev == total_txn))
    checks.append(("net_movement > 0 (deposits > withdrawals)", net > 0))

    # ------------------------------------------------------------------ #
    # 14-16  GL reconciliation output                                     #
    # ------------------------------------------------------------------ #
    recon     = results.get("recon")
    recon_out = recon.output if (recon and recon.ok) else {}

    matched    = recon_out.get("matched_count", 0)
    bank_count = recon_out.get("bank_txn_count", 0)
    is_recon   = recon_out.get("is_reconciled")

    checks.append(("Reconcile matched_count > 0",       matched > 0))
    checks.append(("Reconcile bank_txn_count == 4",     bank_count == 4))
    checks.append(("Reconcile is_reconciled is a bool", isinstance(is_recon, bool)))

    # ------------------------------------------------------------------ #
    # 17-19  HST return output                                            #
    # ------------------------------------------------------------------ #
    hst     = results.get("hst")
    hst_out = hst.output if (hst and hst.ok) else {}

    line_103  = Decimal(str(hst_out.get("line_103_hst_collected", "0")))
    line_109  = Decimal(str(hst_out.get("line_109_net_tax",       "0")))
    due_date  = hst_out.get("filing_due_date", "")

    checks.append(("HST line_103 (collected) > 0",    line_103 > 0))
    checks.append(("HST line_109 (net tax) > 0",      line_109 > 0))
    checks.append(("HST filing_due_date == 2026-01-31", str(due_date) == "2026-01-31"))

    # ------------------------------------------------------------------ #
    # 20-21  RAG output                                                   #
    # ------------------------------------------------------------------ #
    idx_out  = results.get("index")
    idx_data = idx_out.output if (idx_out and idx_out.ok) else {}
    rag_out  = results.get("rag")
    rag_data = rag_out.output if (rag_out and rag_out.ok) else {}

    checks.append(("INDEX_DOCUMENT chunks_indexed > 0",
                   int(idx_data.get("chunks_indexed", 0)) > 0))
    checks.append(("RAG_QUERY context is non-empty",
                   bool(rag_data.get("context"))))

    # ------------------------------------------------------------------ #
    # 22-24  Email output                                                 #
    # ------------------------------------------------------------------ #
    send_mock = gmail_mock.users.return_value.messages.return_value.send
    send_call_count = send_mock.call_count

    checks.append(("Gmail send() called exactly once", send_call_count == 1))

    raw_b64   = ""
    email_raw = ""
    if send_call_count >= 1:
        try:
            raw_b64   = send_mock.call_args[1]["body"]["raw"]
            padded    = raw_b64 + "=" * (-len(raw_b64) % 4)
            email_raw = base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
        except Exception:
            pass

    checks.append(("Email subject contains '2025-12'",
                   "2025-12" in email_raw))
    checks.append(("Email body contains HST net-tax '5,080'",
                   "5,080" in email_raw))

    # ------------------------------------------------------------------ #
    # 25-27  Pipeline integrity                                           #
    # ------------------------------------------------------------------ #
    all_keys = {"index", "bookkeeping", "recon", "hst", "rag", "email"}
    checks.append(("All 6 result keys present",
                   all_keys.issubset(results.keys())))
    checks.append(("session_id is a non-empty string",
                   bool(results.get("session_id"))))
    checks.append(("elapsed_ms > 0",
                   int(results.get("elapsed_ms", 0)) > 0))

    # ------------------------------------------------------------------ #
    # Report                                                              #
    # ------------------------------------------------------------------ #
    passed = sum(1 for _, ok in checks if ok)
    total  = len(checks)
    print(f"\nP2.6 Monthly Close smoke test -- {passed}/{total} checks passed\n")
    for i, (label, ok) in enumerate(checks, 1):
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {i:2d}  {label}")

    if passed < total:
        print(f"\n{total - passed} check(s) FAILED.")
        sys.exit(1)
    else:
        print("\nAll checks passed.")


if __name__ == "__main__":
    run()
