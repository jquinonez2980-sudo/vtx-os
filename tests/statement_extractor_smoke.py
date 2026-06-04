"""
tests/statement_extractor_smoke.py
Offline smoke test for sage50/statement_extractor.py (Session 14 wiring).

OFFLINE: no GCP / Document AI calls. Fake extraction-path functions are
injected via _PATH_ORDER so the cascade logic is exercised deterministically.

Checks:
   1   parse_ocr_text parses the fixture text into 2 transactions (fixture valid)
   2   detect_bank identifies TD from the fixture header
   3   Cascade is parse-aware: a high-confidence path that yields 0 txns is
       skipped in favour of the next path that yields >0 txns
   4   Winning result carries the parsed transactions (txn_count == 2)
   5   force_path bypasses the cascade (returns PyMuPDF even with 0 txns)
   6   Sign convention: credit -> positive amount, debit -> negative amount
   7   to_dataframe preserves Decimal (no float coercion)
   8   extract_to_csv writes the expected row count
"""

from __future__ import annotations

import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sage50.statement_extractor as se
from sage50.statement_extractor import (
    BankStatementExtractor,
    ExtractionPath,
    _txns_to_bank_transactions,
)
from sage50.bank_statement_ocr_parser import _Txn, detect_bank, parse_ocr_text
from models.banking import BankCode


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Parseable TD-style OCR text (row-reconstructed: DESCRIPTION AMOUNT DATE BALANCE)
GOOD_TEXT = (
    "TD CANADA TRUST\n"
    "ACCOUNT STATEMENT\n"
    "DESCRIPTION  AMOUNT  DATE  BALANCE\n"
    "BALANCE FORWARD  1,000.00\n"
    "PAYMENT TO VENDOR  100.00  JAN05  900.00\n"
    "DEPOSIT FROM CLIENT  500.00  JAN10  1,400.00\n"
)

# High character-density but contains no parseable transaction lines, so it
# scores well on density yet parses to 0 transactions.
NOISE_TEXT = ("the quick brown fox jumps over the lazy dog " * 30) + "\n"


def _fake_pymupdf(_path: Path) -> tuple[str, float, int, list[str]]:
    return NOISE_TEXT, 0.90, 1, [NOISE_TEXT]   # high conf, 0 txns


def _fake_pdfplumber(_path: Path) -> tuple[str, float, int, list[str]]:
    return GOOD_TEXT, 0.90, 1, [GOOD_TEXT]     # high conf, 2 txns


def _fake_docai(_path: Path) -> tuple[str, float, int, list[str]]:
    return "", 0.0, 0, []


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

_passed = 0
_failed = 0


def check(label: str, cond: bool) -> None:
    global _passed, _failed
    mark = "PASS" if cond else "FAIL"
    if cond:
        _passed += 1
    else:
        _failed += 1
    print(f"  [{mark}] {label}")


def main() -> int:
    # A real PDF isn't needed — extract() only requires the path to exist.
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        fh.write(b"%PDF-1.4 fake")
        pdf_path = Path(fh.name)

    # 1 — fixture is genuinely parseable
    raw = parse_ocr_text(GOOD_TEXT, bank=BankCode.TD)
    check("fixture parses to 2 transactions", len(raw) == 2)

    # 2 — bank detection
    check("detect_bank -> TD", detect_bank(GOOD_TEXT) is BankCode.TD)

    # Inject fake paths for deterministic cascade
    orig_order = se._PATH_ORDER
    orig_fns = se._PATH_FNS
    se._PATH_ORDER = [
        (ExtractionPath.PYMUPDF, _fake_pymupdf),
        (ExtractionPath.PDFPLUMBER, _fake_pdfplumber),
        (ExtractionPath.DOCAI, _fake_docai),
    ]
    se._PATH_FNS = dict(se._PATH_ORDER)
    try:
        # 3 + 4 — parse-aware cascade skips the dense-but-empty PyMuPDF result
        result = BankStatementExtractor().extract(pdf_path)
        check("cascade skips 0-txn path -> pdfplumber", result.path_used is ExtractionPath.PDFPLUMBER)
        check("winning result carries 2 transactions", result.txn_count == 2)

        # 5 — force_path bypasses the cascade
        forced = BankStatementExtractor(force_path=ExtractionPath.PYMUPDF).extract(pdf_path)
        check("force_path returns PyMuPDF (0 txns)", forced.path_used is ExtractionPath.PYMUPDF and forced.txn_count == 0)

        # 8 — extract_to_csv writes 2 rows
        csv_out = pdf_path.with_suffix(".csv")
        BankStatementExtractor().extract_to_csv(pdf_path, csv_out)
        rows = [ln for ln in csv_out.read_text(encoding="utf-8").splitlines() if ln.strip()]
        check("extract_to_csv wrote 2 data rows", len(rows) - 1 == 2)  # minus header
        csv_out.unlink(missing_ok=True)
    finally:
        se._PATH_ORDER = orig_order
        se._PATH_FNS = orig_fns
        pdf_path.unlink(missing_ok=True)

    # 9 — OCR row-split recovery: a TD scan can wrap a transaction's date onto
    # its own bare line (amount stays above). The fee must still be captured.
    wrap_text = (
        "BALANCE FORWARD  JAN30  5,000.00\n"
        "MONTHLY PLAN FEE  19.00\n"
        "FEB27\n"
    )
    wrap = parse_ocr_text(wrap_text, bank=BankCode.TD)
    check(
        "wrapped date row-split captures the fee",
        len(wrap) == 1 and wrap[0].debit == Decimal("19.00")
        and wrap[0].txn_date.month == 2 and wrap[0].txn_date.day == 27,
    )

    # 6 — sign convention
    txns = _txns_to_bank_transactions(
        [
            _Txn(txn_date=raw[0].txn_date, description="OUT", debit=Decimal("100.00"), credit=Decimal("0"), balance=None),
            _Txn(txn_date=raw[0].txn_date, description="IN", debit=Decimal("0"), credit=Decimal("500.00"), balance=None),
        ],
        BankCode.TD, "xxxx",
    )
    check("debit -> negative amount", txns[0].amount == Decimal("-100.00"))
    check("credit -> positive amount", txns[1].amount == Decimal("500.00"))

    # 7 — to_dataframe preserves Decimal (skips if pandas not installed)
    try:
        df = BankStatementExtractor.to_dataframe(txns)
        check("to_dataframe keeps Decimal amount", isinstance(df["amount"].iloc[0], Decimal))
    except ModuleNotFoundError:
        print("  [SKIP] to_dataframe Decimal check (pandas not installed)")

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
