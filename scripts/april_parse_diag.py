"""Run the OCR parser against the saved April reconstructed text and show
exactly which lines become transactions vs. get dropped, plus how the
transaction-section slicer trims the input.
"""
from __future__ import annotations

from pathlib import Path

from sage50.bank_statement_ocr_parser import (
    detect_bank, _infer_year, _extract_transaction_section,
    _parse_lines, parse_ocr_text,
)

TXT = Path(r"C:\Users\JorgeJr\vtx-os\data\test-client\april-ocr\reconstructed.txt")


def main() -> None:
    text = TXT.read_text(encoding="utf-8")
    print(f"total chars: {len(text):,}")
    bank = detect_bank(text)
    year = _infer_year(text)
    print(f"bank={bank}  year={year}")

    all_lines = text.splitlines()
    section = _extract_transaction_section(all_lines)
    print(f"all lines: {len(all_lines)}  section lines: {len(section)}")
    print(f"section[0]: {section[0]!r}")
    print(f"section[-1]: {section[-1]!r}")

    txns = _parse_lines(section, year)
    print(f"\nparsed {len(txns)} transactions:")
    for t in txns:
        print(f"  {t.txn_date}  D={t.debit}  C={t.credit}  bal={t.balance}  {t.description[:40]}")

    # Show full section so we can eyeball missed rows
    print("\n=== FULL SECTION ===")
    for idx, ln in enumerate(section):
        print(f"{idx:3} | {ln}")


if __name__ == "__main__":
    main()
