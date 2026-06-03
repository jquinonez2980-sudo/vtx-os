"""
sage50/cheque_extractor.py
Extract payee names and cheque numbers from cheque image pages in bank statement PDFs.

TD Bank scanned statements include cleared cheque face images as PDF pages after
the transaction ledger.  When Document AI OCRs those pages the text contains:
  - Payee name after "Pay to the order of ..."
  - Cheque number in "No. NNNNN" label (top-right of cheque face)
  - Cheque number again in the MICR line at the bottom: "|| NNNNN |"
  - Dollar amount in numeric box (cross-validation)

The cheque number in the MICR line matches the number in the bank statement
description: "CHQ#00788-1141529082" → cheque 00788.

Public API:
    extract_cheque_map(page_texts) -> dict[str, ChequeInfo]
        page_texts: list[str] from ExtractionResult.page_texts (one per PDF page).
        Returns {cheque_no: ChequeInfo} keyed by zero-padded 5-digit cheque number.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Page classification
# ---------------------------------------------------------------------------

# These markers appear on transaction ledger pages — if present, it's NOT a cheque page.
_TXN_PAGE_RE = re.compile(
    r"balance\s+forward|opening\s+balance|brought\s+forward"
    r"|\bwithdrawals\b|\bdeposits\b|\bdescription\b",
    re.I,
)

# At least one of these must appear for a page to be treated as a cheque image.
_CHEQUE_PAGE_RE = re.compile(
    r"pay\s+to"            # payee label
    r"|\|\s*\d{4,6}\s*\|", # MICR delimiter around cheque number
    re.I,
)


def _is_cheque_page(text: str) -> bool:
    """Return True if page text looks like a cleared cheque image, not a ledger page."""
    if _TXN_PAGE_RE.search(text):
        return False
    return bool(_CHEQUE_PAGE_RE.search(text))


# ---------------------------------------------------------------------------
# Per-cheque field extraction
# ---------------------------------------------------------------------------

# "Pay to the order of ROGERS COMMUNICATIONS INC.   457.13"
# Capture payee up to the first dollar amount or end-of-text on that line.
_PAYEE_RE = re.compile(
    r"pay\s+to\s+(?:the\s+)?(?:order\s+of\s+)?(.+?)(?=\s+\$|\s+\d{1,3}(?:,\d{3})*\.\d{2}|\n|\Z)",
    re.I,
)

# "No. 00788" or "No 00788" (label printed on cheque face, top-right)
_CHQNO_LABEL_RE = re.compile(r"\bNo\.?\s*#?\s*(\d{4,6})\b", re.I)

# MICR trailing field: "|| 00788 |"  (cheque number is the LAST field in Canadian MICR)
_CHQNO_MICR_RE = re.compile(r"\|\|\s*(\d{4,6})\s*\|")

# Dollar amount with mandatory two decimal places
_AMOUNT_RE = re.compile(r"\b(\d{1,3}(?:,\d{3})*\.\d{2})\b")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class ChequeInfo:
    cheque_no:  str            # zero-padded to 5 digits, e.g. "00788"
    payee:      str            # payee name as OCR'd, e.g. "Rogers Communications Inc."
    amount:     Decimal | None # numeric amount from cheque face (may differ slightly from txn)
    confidence: float          # 0.0–1.0 (see _parse_cheque_segment for rules)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_cheque_segment(seg: str) -> ChequeInfo | None:
    """Parse one cheque's worth of OCR text and return ChequeInfo or None."""
    # Payee — required field; skip segment if missing
    m_payee = _PAYEE_RE.search(seg)
    if not m_payee:
        return None
    payee = m_payee.group(1).strip().rstrip(".,;:")
    if not payee or len(payee) < 3:
        return None

    # Cheque number — prefer MICR trail (more reliable than label)
    cheque_no: str | None = None
    m_micr = _CHQNO_MICR_RE.search(seg)
    if m_micr:
        cheque_no = m_micr.group(1).zfill(5)
    else:
        m_label = _CHQNO_LABEL_RE.search(seg)
        if m_label:
            cheque_no = m_label.group(1).zfill(5)

    # Dollar amount — look in the first 600 chars (cheque face area)
    amount: Decimal | None = None
    for raw in _AMOUNT_RE.findall(seg[:600]):
        try:
            amount = Decimal(raw.replace(",", ""))
        except InvalidOperation:
            pass
        # Stop at the first plausible amount (not a zip code or date fragment)
        if amount and amount >= Decimal("1.00"):
            break
        amount = None

    # Confidence:
    #   1.0  payee + cheque_no + amount all found
    #   0.7  payee + cheque_no (amount garbled)
    #   0.5  payee + amount (cheque_no unreadable — will try to match by amount later)
    #   0.3  payee only
    if cheque_no and amount:
        confidence = 1.0
    elif cheque_no:
        confidence = 0.7
    elif amount:
        confidence = 0.5
    else:
        confidence = 0.3

    return ChequeInfo(
        cheque_no=cheque_no or "",
        payee=payee,
        amount=amount,
        confidence=confidence,
    )


def _parse_cheque_page(text: str) -> list[ChequeInfo]:
    """Parse one PDF page that may contain 1 or 2 stacked cheque images."""
    # Split on each "Pay to" occurrence — two cheques on one page produce two segments.
    # The split uses a lookahead so the delimiter stays at the start of each segment.
    segments = re.split(r"(?=pay\s+to)", text, flags=re.I)
    results: list[ChequeInfo] = []
    for seg in segments:
        if not re.search(r"pay\s+to", seg, re.I):
            continue
        info = _parse_cheque_segment(seg)
        if info is not None:
            results.append(info)
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_cheque_map(page_texts: list[str]) -> dict[str, ChequeInfo]:
    """Build a {cheque_no: ChequeInfo} map from per-page OCR text.

    Args:
        page_texts: One string per PDF page, from ExtractionResult.page_texts.
                    Pages that are transaction ledger pages are automatically
                    skipped; only cheque image pages are parsed.

    Returns:
        dict keyed by zero-padded 5-digit cheque number string.
        When confidence < 0.5 the cheque_no may be empty ("") — those entries
        are included so callers can log unmatched cheques.
        When the same cheque_no appears on multiple pages (unlikely), the
        highest-confidence entry is kept.
    """
    result: dict[str, ChequeInfo] = {}
    cheque_pages = 0
    for page_text in page_texts:
        if not _is_cheque_page(page_text):
            continue
        cheque_pages += 1
        for info in _parse_cheque_page(page_text):
            key = info.cheque_no or f"__no_no_{len(result)}"
            existing = result.get(key)
            if existing is None or info.confidence > existing.confidence:
                result[key] = info

    if cheque_pages:
        matched = sum(1 for k in result if not k.startswith("__"))
        log.info(
            "cheque_extractor: %d cheque page(s), %d payee(s) extracted (%d matched by cheque_no)",
            cheque_pages, len(result), matched,
        )
    return result
