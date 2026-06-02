"""
scripts/_debug_feb_ocr.py
Dev-only: capture the Feb 2026 statement OCR text and diagnose the missing
$19.00 MONTHLY PLAN FEE / service charge transaction.

Fetches the first unread Gmail PDF, OCRs it (cached to data/test-client so we
don't repeat the ~5 min call), then prints:
  - lines mentioning 19.00 / service / charge / plan fee / feb 27
  - where _extract_transaction_section sliced the line list

    python scripts/_debug_feb_ocr.py                   # Gmail OCR + cache + diagnose
    python scripts/_debug_feb_ocr.py --cached          # reuse cached OCR text
    python scripts/_debug_feb_ocr.py --pdf PATH        # OCR a local PDF (no Gmail)
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

_CACHE = _ROOT / "data" / "test-client" / "feb-2026-ocr.txt"


def _cache_for(name: str) -> Path:
    return _ROOT / "data" / "test-client" / f"{name}-ocr.txt"


def _get_text() -> str:
    from core.docai_ocr import ocr_pdf_bytes

    # Local-PDF mode: bypass Gmail entirely.
    if "--pdf" in sys.argv:
        pdf_path = Path(sys.argv[sys.argv.index("--pdf") + 1]).resolve()
        cache = _cache_for(pdf_path.stem.lower())
        if "--cached" in sys.argv and cache.exists():
            print(f"Using cached OCR text: {cache}")
            return cache.read_text(encoding="utf-8")
        print(f"OCR (local): {pdf_path.name} ({pdf_path.stat().st_size:,} bytes)...")
        text = ocr_pdf_bytes(pdf_path.read_bytes())
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
        print(f"Cached OCR text -> {cache} ({len(text):,} chars)")
        return text

    if "--cached" in sys.argv and _CACHE.exists():
        print(f"Using cached OCR text: {_CACHE}")
        return _CACHE.read_text(encoding="utf-8")

    from core.gmail_notifier import GmailNotifier

    notifier = GmailNotifier()
    msgs = notifier.poll_for_pdf_attachments()
    if not msgs:
        print("No unread inbox PDF.")
        raise SystemExit(1)
    msg, att = msgs[0], msgs[0]["attachments"][0]
    print(f"OCR: {att['filename']} ({att['size']:,} bytes) — this takes ~5 min...")
    with tempfile.TemporaryDirectory() as tmp:
        pdf = notifier.save_attachment(msg["msg_id"], att["attachment_id"], att["filename"], Path(tmp))
        text = ocr_pdf_bytes(pdf.read_bytes())
    _CACHE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE.write_text(text, encoding="utf-8")
    print(f"Cached OCR text → {_CACHE} ({len(text):,} chars)")
    return text


def main() -> int:
    from sage50.bank_statement_ocr_parser import (
        _extract_transaction_section, _TXN_END_RE, parse_ocr_text,
    )
    from models.banking import BankCode

    text = _get_text()
    lines = text.splitlines()
    print(f"\nTotal OCR lines: {len(lines)}")

    pat = re.compile(r"19\.00|service|charge|plan\s*fee|feb\s*27", re.I)
    print("\n--- Lines matching 19.00 / service / charge / plan fee / feb 27 ---")
    for i, ln in enumerate(lines):
        if pat.search(ln):
            print(f"  L{i:>4}: {ln!r}")

    section = _extract_transaction_section(lines)
    # Find the index in the original list where the section ends
    print(f"\nSection sliced to {len(section)} lines (of {len(lines)}).")
    print("--- _TXN_END_RE markers (these truncate the section) ---")
    for i, ln in enumerate(lines):
        if _TXN_END_RE.search(ln):
            print(f"  L{i:>4}: {ln!r}")

    txns = parse_ocr_text(text, bank=BankCode.TD)
    print(f"\nparse_ocr_text → {len(txns)} transactions")
    has_19 = any(t.debit == __import__("decimal").Decimal("19.00")
                 or t.credit == __import__("decimal").Decimal("19.00") for t in txns)
    print(f"$19.00 transaction present: {has_19}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
