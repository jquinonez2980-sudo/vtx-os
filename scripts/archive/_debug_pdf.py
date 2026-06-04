"""
Debug a bank statement PDF — dumps raw OCR text and the extracted CSV side by side.

Usage:
    python scripts/_debug_pdf.py <path/to/statement.pdf>

Writes:
    <pdf_name>-raw.txt     raw text extracted by pdfplumber (all pages)
    <pdf_name>-debug.csv   output of pdf_extractor.extract_to_csv()

Also prints a summary to stdout so you can see what went wrong without opening files.
"""
import sys, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if len(sys.argv) < 2:
    print("Usage: python scripts/_debug_pdf.py <statement.pdf>")
    sys.exit(1)

pdf_path = Path(sys.argv[1])
if not pdf_path.exists():
    print(f"File not found: {pdf_path}")
    sys.exit(1)

# ── 1. Raw OCR text ──────────────────────────────────────────────────────────
import pdfplumber

raw_out = pdf_path.with_name(pdf_path.stem + "-raw.txt")
page_texts = []
with pdfplumber.open(str(pdf_path)) as doc:
    print(f"PDF has {len(doc.pages)} page(s).")
    for i, page in enumerate(doc.pages):
        text = page.extract_text() or ""
        page_texts.append(text)
        has_tx = "BALANCE FORWARD" in text.upper() or "DESCRIPTION" in text.upper()
        print(f"  Page {i+1}: {len(text)} chars  page_filter={'PASS' if has_tx else 'FAIL'}")
        if text:
            print("  First 300 chars:")
            for line in text[:300].splitlines():
                print(f"    {line}")

raw_out.write_text(
    "\n".join(f"=== PAGE {i+1} ===\n{t}" for i, t in enumerate(page_texts)),
    encoding="utf-8",
)
print(f"\nRaw text written to: {raw_out}")

# ── 2. Extract to CSV ────────────────────────────────────────────────────────
from sage50.pdf_extractor import extract_to_csv

csv_out = pdf_path.with_name(pdf_path.stem + "-debug.csv")
try:
    extract_to_csv(str(pdf_path), str(csv_out))
except Exception as exc:
    print(f"\nextract_to_csv raised: {exc}")
    sys.exit(1)

csv_bytes = csv_out.stat().st_size
csv_content = csv_out.read_text(encoding="utf-8", errors="replace")
print(f"\nCSV written to: {csv_out}  ({csv_bytes} bytes)")
print("\nFull CSV content:")
print("-" * 60)
print(csv_content)
print("-" * 60)

# ── 3. Try bank_parser ───────────────────────────────────────────────────────
from sage50.bank_parser import parse_csv

try:
    txns = parse_csv(str(csv_out), account_no="xxxx5443")
    print(f"\nparse_csv found {len(txns)} transactions.")
    for t in txns[:5]:
        print(f"  {t.txn_date}  {t.amount:>12}  {t.description[:50]}")
except Exception as exc:
    print(f"\nparse_csv raised: {exc}")
