"""
scripts/_validate_extractor.py
Dev-only live validation for the wired BankStatementExtractor (Session 14).

Pulls the first unread PDF from Gmail, then:
  1. benchmark()  — times PyMuPDF vs pdfplumber vs Document AI on the file
  2. extract()    — confirms which path wins and how many transactions parse

Requires ADC + Gmail OAuth + vtx-docai-processor-id secret (live GCP calls).

    python scripts/_validate_extractor.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def main() -> int:
    from core.gmail_notifier import GmailNotifier
    from sage50.statement_extractor import BankStatementExtractor, benchmark

    notifier = GmailNotifier()
    msgs = notifier.poll_for_pdf_attachments()
    if not msgs:
        print("No unread inbox emails with PDF attachments.")
        return 1

    msg = msgs[0]
    att = msg["attachments"][0]
    print(f"Email   : {msg['subject']!r} from {msg['from']}")
    print(f"PDF     : {att['filename']} ({att['size']:,} bytes)\n")

    with tempfile.TemporaryDirectory(prefix="vtx_val_") as tmp:
        pdf_path = notifier.save_attachment(
            msg["msg_id"], att["attachment_id"], att["filename"], Path(tmp)
        )

        # 1 — benchmark paths ( --skip-docai avoids the slow live DocAI call)
        skip_docai = "--skip-docai" in sys.argv
        benchmark(pdf_path, skip_docai=skip_docai)
        if skip_docai:
            print("(skipped DocAI + extract() — local-path validation only)")
            return 0

        # 2 — confirm the cascade's chosen path
        result = BankStatementExtractor().extract(pdf_path)
        print(
            f"extract(): winning path = {result.path_used.value}  "
            f"conf={result.confidence:.2f}  pages={result.pages}  "
            f"{result.elapsed_ms} ms  bank={result.bank_code.value if result.bank_code else '?'}  "
            f"txns={result.txn_count}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
