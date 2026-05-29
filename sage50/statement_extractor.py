"""
sage50/statement_extractor.py
High-performance bank statement PDF → BankTransaction extractor.

Extraction paths tried in order, first to exceed the confidence threshold wins:

  1. PyMuPDF (fitz)   — 10–50 ms/page. Ideal for digitally-created PDFs
                        (online banking downloads, e-statements).
  2. pdfplumber       — 200–800 ms/page. Handles complex digital layouts
                        where PyMuPDF produces disorganised column text.
  3. Document AI OCR  — 10–90 s total (sync <5 MB / async-batch ≥5 MB).
                        Last resort for scanned or image-only PDFs.

Text from any path is parsed by bank_statement_ocr_parser.parse_ocr_text(),
which auto-detects the bank and handles all seven supported Canadian formats.
The resulting _Txn records are converted to BankTransaction objects that
slot directly into the existing BookkeepingAgent pipeline.

Usage:
    from sage50.statement_extractor import BankStatementExtractor

    extractor = BankStatementExtractor()
    txns = extractor.extract_transactions("data/statement.pdf")
    df   = BankStatementExtractor.to_dataframe(txns)

Force a specific extraction path:
    extractor = BankStatementExtractor(force_path=ExtractionPath.DOCAI)

Batch processing (thread-pool, I/O-bound):
    from sage50.statement_extractor import extract_batch
    results = extract_batch(["jan.pdf", "feb.pdf", "mar.pdf"])

Benchmark all paths on one file:
    from sage50.statement_extractor import benchmark
    print(benchmark("data/statement.pdf"))
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Sequence

from models.banking import BankCode, BankTransaction

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Minimum character-density score (0–1) to accept a path's output without
# trying the next path.  Lower = accept noisier text.
_CONF_THRESHOLD_DEFAULT: float = 0.40

# Characters-per-page range used for normalisation.
# Digital bank statements: 500–3 000 chars/page.
# Scanned/image PDFs:     0–50 chars/page (noise only).
_CONF_LOW:  float = 50.0
_CONF_HIGH: float = 500.0


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class ExtractionPath(str, Enum):
    PYMUPDF    = "pymupdf"
    PDFPLUMBER = "pdfplumber"
    DOCAI      = "docai"


@dataclass
class ExtractionResult:
    text:         str
    path_used:    ExtractionPath
    confidence:   float
    pages:        int
    elapsed_ms:   int
    bank_code:    BankCode | None = None
    transactions: list = field(default_factory=list)  # list[bank_statement_ocr_parser._Txn]

    @property
    def txn_count(self) -> int:
        return len(self.transactions)


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------

def _confidence(text: str, page_count: int) -> float:
    """Score extracted text quality as a value in [0, 1].

    Uses printable-character density per page as the primary signal.
    Returns 0.0 for empty text or zero-page documents.
    """
    if page_count <= 0 or not text.strip():
        return 0.0
    density = len(text.strip()) / page_count
    return min(1.0, max(0.0, (density - _CONF_LOW) / (_CONF_HIGH - _CONF_LOW)))


# ---------------------------------------------------------------------------
# Extraction path implementations
# ---------------------------------------------------------------------------

def _extract_pymupdf(path: Path) -> tuple[str, float, int]:
    """Extract native text layer with PyMuPDF (fitz).

    Uses sort=True (available since PyMuPDF 1.19) to guarantee reading order
    even on PDFs with out-of-order internal text streams.  Falls back
    gracefully if the parameter is not supported.

    Returns (text, confidence, page_count).
    """
    import fitz  # PyMuPDF

    doc = fitz.open(str(path))
    try:
        pages: list[str] = []
        for page in doc:
            try:
                t = page.get_text("text", sort=True)
            except TypeError:
                t = page.get_text("text")
            if t:
                pages.append(t)
        n = len(doc)
    finally:
        doc.close()

    full = "\n".join(pages)
    return full, _confidence(full, n), n


def _extract_pdfplumber(path: Path) -> tuple[str, float, int]:
    """Extract text using pdfplumber.

    x_tolerance=3 tightens column separation, which helps avoid run-together
    description + amount tokens in narrow bank statement tables.

    Returns (text, confidence, page_count).
    """
    import pdfplumber

    parts: list[str] = []
    n = 0
    with pdfplumber.open(str(path)) as pdf:
        n = len(pdf.pages)
        for page in pdf.pages:
            t = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            parts.append(t)

    full = "\n".join(parts)
    return full, _confidence(full, n), n


def _extract_docai(path: Path) -> tuple[str, float, int]:
    """Extract text via Google Cloud Document AI OCR.

    Routing: <5 MB → synchronous process_document;
             ≥5 MB → async batch_process_documents (GCS round-trip).

    Requires vtx-docai-processor-id secret and GCP ADC credentials.
    Returns (text, confidence, page_count).
    """
    from core.docai_ocr import ocr_pdf_file

    text = ocr_pdf_file(path)
    # DocAI output quality is high when non-empty; use 0.95 as the score so
    # callers know this path succeeded and further fallback isn't needed.
    conf = 0.95 if text.strip() else 0.0
    # Approximate page count from form-feed separators; minimum 1.
    pages = max(1, text.count("\f") + 1)
    return text, conf, pages


# ---------------------------------------------------------------------------
# _Txn → BankTransaction conversion
# ---------------------------------------------------------------------------

def _txns_to_bank_transactions(
    raw_txns: list[Any],   # list[bank_statement_ocr_parser._Txn]
    bank_code: BankCode,
    account_no: str,
) -> list[BankTransaction]:
    """Convert bank_statement_ocr_parser._Txn objects to BankTransaction models.

    Applies the sign convention used throughout VTX-OS:
        positive amount = money IN (deposit / credit)
        negative amount = money OUT (withdrawal / debit)
    """
    result: list[BankTransaction] = []
    for i, t in enumerate(raw_txns):
        amount: Decimal = t.credit - abs(t.debit)
        key = f"{bank_code.value}|{account_no}|{t.txn_date}|{t.description}|{amount}|{i}"
        txn_id = hashlib.sha256(key.encode()).hexdigest()[:20]
        result.append(BankTransaction(
            txn_id=txn_id,
            bank_code=bank_code,
            account_no=account_no,
            txn_date=t.txn_date,
            description=t.description,
            raw_description=t.description,
            amount=amount,
            balance=t.balance,
        ))
    return result


# ---------------------------------------------------------------------------
# BankStatementExtractor
# ---------------------------------------------------------------------------

class BankStatementExtractor:
    """Multi-path bank statement PDF → BankTransaction extractor.

    Tries PyMuPDF → pdfplumber → Document AI OCR in order and stops at
    the first path whose text quality exceeds *confidence_threshold*.

    Args:
        confidence_threshold: Text quality score in [0, 1] required to
            accept a path without trying the next one.  Default 0.40.
            Lower values trust noisier text; higher values force more
            expensive paths for borderline PDFs.
        force_path: If set, skip straight to this path (useful for testing
            or when you know the PDF type in advance).
    """

    def __init__(
        self,
        confidence_threshold: float = _CONF_THRESHOLD_DEFAULT,
        force_path: ExtractionPath | None = None,
    ) -> None:
        self._threshold = confidence_threshold
        self._force_path = force_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_transactions(
        self,
        pdf_path: str | Path,
        bank: str = "auto",
        account_no: str = "xxxx",
    ) -> list[BankTransaction]:
        """Extract bank transactions from a PDF statement.

        Args:
            pdf_path:   Path to the PDF file (local path only).
            bank:       BankCode string ("TD", "RBC", "BMO", "CIBC",
                        "SCOTIABANK", "NATIONAL", "DESJARDINS") or "auto"
                        to detect from statement text.
            account_no: Masked account identifier (e.g. last 4 digits).
                        Used for txn_id hashing only — never stored in full.

        Returns:
            List of BankTransaction objects in statement order.

        Raises:
            FileNotFoundError: pdf_path does not exist.
            RuntimeError:      All extraction paths failed.
        """
        result = self.extract(pdf_path, bank=bank)
        if not result.text.strip():
            raise RuntimeError(
                f"All extraction paths produced empty text for {Path(pdf_path).name}"
            )

        bank_code = result.bank_code or BankCode.GENERIC
        txns = _txns_to_bank_transactions(result.transactions, bank_code, account_no)

        log.info(
            "%s: extracted via %s (conf=%.2f, %d pages, %d ms) → %d transactions (bank=%s)",
            Path(pdf_path).name, result.path_used.value,
            result.confidence, result.pages, result.elapsed_ms,
            len(txns), bank_code.value,
        )
        return txns

    def extract_to_csv(
        self,
        pdf_path: str | Path,
        csv_path: str | Path,
        bank: str = "auto",
        account_no: str = "xxxx",
    ) -> Path:
        """Extract transactions and write a CSV compatible with bank_parser.parse_csv().

        Output columns:  Date, Description, Debit, Credit, Balance
        These headers are auto-detected as BankCode.CIBC by bank_parser.py,
        which computes:  amount = Credit − abs(Debit).

        Returns the path to the written CSV.
        """
        from sage50.bank_statement_ocr_parser import write_csv

        result = self.extract(pdf_path, bank=bank)
        if not result.text.strip():
            log.warning(
                "%s: all extraction paths produced empty text — writing 0 rows",
                Path(pdf_path).name,
            )
        n = write_csv(result.transactions, csv_path)

        log.info(
            "%s: wrote %d rows → %s (path=%s, conf=%.2f)",
            Path(pdf_path).name, n, Path(csv_path).name,
            result.path_used.value, result.confidence,
        )
        return Path(csv_path)

    @staticmethod
    def to_dataframe(transactions: list[BankTransaction]) -> "pd.DataFrame":
        """Convert BankTransaction list to a pandas DataFrame.

        Columns: txn_date, description, amount, balance,
                 bank_code, account_no, txn_id, reference

        amount and balance are kept as Decimal (object dtype) — VTX-OS never
        coerces money to float. Cast at the call site if you need float math.
        """
        import pandas as pd

        if not transactions:
            return pd.DataFrame(columns=[
                "txn_date", "description", "amount", "balance",
                "bank_code", "account_no", "txn_id", "reference",
            ])
        return pd.DataFrame([
            {
                "txn_date":    t.txn_date,
                "description": t.description,
                "amount":      t.amount,
                "balance":     t.balance,
                "bank_code":   t.bank_code.value,
                "account_no":  t.account_no,
                "txn_id":      t.txn_id,
                "reference":   t.reference,
            }
            for t in transactions
        ])

    def extract(self, pdf_path: str | Path, bank: str = "auto") -> ExtractionResult:
        """Run the extraction cascade and return the winning ExtractionResult.

        A path wins when its text quality clears *confidence_threshold* AND it
        parses to at least one transaction. The parse check guards against
        digital PDFs that yield dense-but-unparseable text (e.g. exotic column
        layouts): rather than accepting 0 transactions, the cascade falls
        through to the next, more capable path.

        The returned result carries the parsed `transactions` (list of
        bank_statement_ocr_parser._Txn) and the resolved `bank_code`, so
        callers extract once and reuse — no second extraction or parse.

        Raises FileNotFoundError if *pdf_path* does not exist.
        """
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {path}")

        if self._force_path is not None:
            return self._run_and_parse(path, self._force_path, _PATH_FNS[self._force_path], bank)

        best: ExtractionResult | None = None
        for ep, fn in _PATH_ORDER:
            result = self._run_and_parse(path, ep, fn, bank)
            if best is None or result.confidence > best.confidence:
                best = result
            if result.confidence >= self._threshold and result.txn_count > 0:
                return result
            log.debug(
                "%s: %s conf=%.2f (thr=%.2f) txns=%d — trying next path",
                path.name, ep.value, result.confidence, self._threshold, result.txn_count,
            )

        # Exhausted all paths — return best effort (highest confidence seen).
        assert best is not None
        log.warning(
            "%s: no path cleared threshold with >0 txns (best=%.2f via %s, %d txns)",
            path.name, best.confidence, best.path_used.value, best.txn_count,
        )
        return best

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_and_parse(
        self,
        path: Path,
        ep: ExtractionPath,
        fn: Any,
        bank: str,
    ) -> ExtractionResult:
        """Run one extraction path, then resolve bank + parse its text.

        Failures (missing dep, OCR error) are caught and returned as an empty
        result so the cascade can move on rather than aborting the whole run.
        """
        t0 = time.perf_counter()
        try:
            text, confidence, pages = fn(path)
        except Exception as exc:
            ms = int((time.perf_counter() - t0) * 1000)
            log.warning("%s: %s failed (%s)", path.name, ep.value, exc)
            return ExtractionResult(text="", path_used=ep, confidence=0.0, pages=0, elapsed_ms=ms)

        bank_code: BankCode | None = None
        txns: list = []
        if text.strip():
            bank_code = self._resolve_bank(text, bank)
            from sage50.bank_statement_ocr_parser import parse_ocr_text
            txns = parse_ocr_text(text, bank=bank_code)

        ms = int((time.perf_counter() - t0) * 1000)
        return ExtractionResult(
            text=text, path_used=ep, confidence=confidence, pages=pages,
            elapsed_ms=ms, bank_code=bank_code, transactions=txns,
        )

    @staticmethod
    def _resolve_bank(text: str, bank: str) -> BankCode:
        if bank == "auto":
            from sage50.bank_statement_ocr_parser import detect_bank
            return detect_bank(text)
        return BankCode(bank.upper())


# Path registry — order defines priority
_PATH_FNS: dict[ExtractionPath, Any] = {
    ExtractionPath.PYMUPDF:    _extract_pymupdf,
    ExtractionPath.PDFPLUMBER: _extract_pdfplumber,
    ExtractionPath.DOCAI:      _extract_docai,
}
_PATH_ORDER: list[tuple[ExtractionPath, Any]] = [
    (ExtractionPath.PYMUPDF,    _extract_pymupdf),
    (ExtractionPath.PDFPLUMBER, _extract_pdfplumber),
    (ExtractionPath.DOCAI,      _extract_docai),
]


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def extract_batch(
    pdf_paths: Sequence[str | Path],
    max_workers: int = 4,
    bank: str = "auto",
    account_no: str = "xxxx",
    confidence_threshold: float = _CONF_THRESHOLD_DEFAULT,
) -> dict[str, list[BankTransaction]]:
    """Extract transactions from multiple PDFs concurrently.

    Uses a thread pool (I/O-bound: PDF reads, optional DocAI HTTPS calls).
    max_workers=4 is a safe default; raise it if all PDFs are digital-native
    and you are not hitting DocAI rate limits.

    Returns:
        dict mapping pdf_path (str) → list[BankTransaction].
        Failed PDFs map to an empty list; errors are logged at WARNING level.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    extractor = BankStatementExtractor(confidence_threshold=confidence_threshold)
    results: dict[str, list[BankTransaction]] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                extractor.extract_transactions, p,
                bank=bank, account_no=account_no,
            ): str(p)
            for p in pdf_paths
        }
        for future in as_completed(futures):
            path_str = futures[future]
            try:
                results[path_str] = future.result()
            except Exception as exc:
                log.error("extract_batch: %s failed: %s", path_str, exc)
                results[path_str] = []

    return results


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

def benchmark(
    pdf_path: str | Path,
    skip_docai: bool = False,
    account_no: str = "xxxx",
) -> dict[str, dict]:
    """Run all extraction paths on one PDF and report speed + accuracy.

    Args:
        pdf_path:   PDF to test.
        skip_docai: Set True to skip the Document AI call (avoids GCP cost
                    when you only want to compare local paths).
        account_no: Passed through to extract_transactions().

    Returns a dict keyed by path name with fields:
        elapsed_ms   — wall-clock time for text extraction + parsing
        txn_count    — number of transactions parsed
        confidence   — text quality score (0–1)
        pages        — page count
        error        — exception message, or None on success
    """
    from sage50.bank_statement_ocr_parser import parse_ocr_text

    path = Path(pdf_path)
    paths: list[tuple[str, Any]] = [
        ("pymupdf",    _extract_pymupdf),
        ("pdfplumber", _extract_pdfplumber),
    ]
    if not skip_docai:
        paths.append(("docai", _extract_docai))

    report: dict[str, dict] = {}
    for name, fn in paths:
        t0 = time.perf_counter()
        try:
            text, conf, pages = fn(path)
            bank_code = BankStatementExtractor._resolve_bank(text, "auto")
            txns_raw = parse_ocr_text(text, bank=bank_code)
            ms = int((time.perf_counter() - t0) * 1000)
            report[name] = {
                "elapsed_ms": ms,
                "txn_count":  len(txns_raw),
                "confidence": round(conf, 3),
                "pages":      pages,
                "error":      None,
            }
        except Exception as exc:
            ms = int((time.perf_counter() - t0) * 1000)
            report[name] = {
                "elapsed_ms": ms,
                "txn_count":  0,
                "confidence": 0.0,
                "pages":      0,
                "error":      str(exc),
            }

    # Print a compact summary table
    header = f"{'Path':<12}  {'ms':>6}  {'txns':>5}  {'conf':>6}  {'pages':>5}  Error"
    print(f"\nBenchmark: {path.name}")
    print("-" * len(header))
    print(header)
    print("-" * len(header))
    for name, r in report.items():
        err = (r["error"] or "")[:40]
        print(
            f"{name:<12}  {r['elapsed_ms']:>6}  {r['txn_count']:>5}  "
            f"{r['confidence']:>6.3f}  {r['pages']:>5}  {err}"
        )
    print()
    return report


# ---------------------------------------------------------------------------
# Module-level convenience shim (mirrors pdf_extractor.extract_to_csv API)
# ---------------------------------------------------------------------------

def extract_to_csv(
    pdf_path: str | Path,
    csv_path: str | Path,
    bank: str = "auto",
    account_no: str = "xxxx",
    confidence_threshold: float = _CONF_THRESHOLD_DEFAULT,
) -> Path:
    """Module-level convenience wrapper around BankStatementExtractor.extract_to_csv().

    Drop-in replacement for sage50.pdf_extractor.extract_to_csv() that supports
    all Canadian banks (not just TD) and uses the fast PyMuPDF primary path.
    """
    return BankStatementExtractor(
        confidence_threshold=confidence_threshold
    ).extract_to_csv(pdf_path, csv_path, bank=bank, account_no=account_no)
