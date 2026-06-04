"""
scripts/_extract_one.py  (one-off helper)
Fetch ONE inbox statement (matched by subject substring), extract it with
BankStatementExtractor, save the parsed CSV to a persistent path, and print
every parsed (date, description, amount) so a categorization ruleset can be
built against the REAL bank memos. Decouples the slow DocAI OCR from booking.

    python scripts/_extract_one.py --match "January 07 2025" \
        --out "R:\\Canadian Federation of theotherapy\\drop\\theotherapy-2025-01.csv"
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    from core.gmail_notifier import GmailNotifier
    from sage50.statement_extractor import BankStatementExtractor
    from sage50.bank_statement_ocr_parser import write_csv

    notifier = GmailNotifier()
    msgs = notifier.poll_for_pdf_attachments()
    needle = args.match.lower()
    hits = [m for m in msgs if needle in m.get("subject", "").lower()]
    if not hits:
        print(f"No message matches {args.match!r}. Subjects:")
        for m in msgs:
            print("  -", m.get("subject", ""))
        return 1
    msg = hits[0]
    print(f"Match: {msg['subject']!r}  from {msg['from']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="vtx_") as tmp:
        att = msg["attachments"][0]
        print(f"Attachment: {att['filename']}  {att['size']:,} bytes")
        pdf = notifier.save_attachment(msg["msg_id"], att["attachment_id"],
                                       att["filename"], Path(tmp))
        print("Extracting (PyMuPDF -> pdfplumber -> DocAI)...", flush=True)
        ext = BankStatementExtractor().extract(pdf)
        print(f"  path={ext.path_used.value} conf={ext.confidence:.2f} "
              f"{ext.pages}p {ext.elapsed_ms} ms  bank={ext.bank_code.value}")
        n = write_csv(ext.transactions, out)
        print(f"  wrote {n} transactions -> {out}")

    print(f"\n{'='*72}\nParsed transactions (date | debit | credit | description):\n{'='*72}")
    for t in ext.transactions:
        d = t.txn_date.isoformat() if getattr(t, "txn_date", None) else "????-??-??"
        deb = str(t.debit) if t.debit else ""
        cred = str(t.credit) if t.credit else ""
        print(f"  {d}  {deb:>10} {cred:>10}  {t.description}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
