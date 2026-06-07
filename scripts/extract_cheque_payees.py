"""
scripts/extract_cheque_payees.py
Re-download bank statement PDFs from Gmail, extract cheque payees via OCR,
and patch _CHEQUE_PAYEES in sage50/categorization_rules.py.

Searches for all emails labelled vtx-processed (already-handled statements)
plus any unread PDF attachments. For each PDF:
  1. Download via Gmail API
  2. Run BankStatementExtractor cascade (PyMuPDF -> pdfplumber -> DocAI)
  3. Run extract_cheque_map(page_texts) to get {cheque_no: ChequeInfo}
  4. Aggregate unique payees across all PDFs (dedup by normalised name)
  5. Auto-assign GL accounts using Concetta's known chart; default to 5900
  6. Patch _CHEQUE_PAYEES in categorization_rules.py (no duplicates)

Usage:
    python scripts/extract_cheque_payees.py --dry-run   # report only, no file change
    python scripts/extract_cheque_payees.py             # report + patch
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from core.gmail_notifier import GmailNotifier
from sage50.cheque_extractor import extract_cheque_map
from sage50.statement_extractor import BankStatementExtractor

RULES_FILE = _ROOT / "sage50" / "categorization_rules.py"

# ---------------------------------------------------------------------------
# GL auto-assignment heuristics for Concetta's chart of accounts
# ---------------------------------------------------------------------------

_GL_HINTS: list[tuple[str, int, str]] = [
    # pattern (uppercase substring)    GL     account name
    ("ROGERS",                         5600, "Telephone & Cellular"),
    ("BELL CANADA",                    5600, "Telephone & Cellular"),
    ("BELL ",                          5600, "Telephone & Cellular"),
    ("TELUS",                          5600, "Telephone & Cellular"),
    ("FIDO",                           5600, "Telephone & Cellular"),
    ("ECONOMICAL",                     5400, "Insurance"),
    ("INTACT",                         5400, "Insurance"),
    ("AVIVA",                          5400, "Insurance"),
    ("SPL LOAN",                       5155, "Car Lease"),
    ("TOYOTA",                         5155, "Car Lease"),
    ("BMW",                            5155, "Car Lease"),
    ("HONDA",                          5155, "Car Lease"),
    ("FORD CREDIT",                    5155, "Car Lease"),
    ("HYDRO ONE",                      5900, "Suspense"),   # no dedicated utilities GL
    ("TORONTO HYDRO",                  5900, "Suspense"),
    ("ENBRIDGE",                       5900, "Suspense"),
    ("UNION GAS",                      5900, "Suspense"),
    ("CITY OF TORONTO",                5900, "Suspense"),
    ("CONCETTA BOSH",                  5850, "Wages & Benefits"),
    ("CHRISTINA BOSH",                 5850, "Wages & Benefits"),
    ("BOSH",                           5850, "Wages & Benefits"),
    ("RECEIVER GENERAL",               2100, "Employee Tax Deductions"),
]


def _guess_gl(payee_upper: str) -> tuple[int, str]:
    for pattern, gl_no, gl_name in _GL_HINTS:
        if pattern in payee_upper:
            return gl_no, gl_name
    return 5900, "Suspense"


def _keyword_for(payee: str) -> str:
    """Return a distinctive uppercase keyword extracted from the payee name.

    Strips legal suffixes and keeps the first 3 significant words.
    e.g. 'Rogers Communications Inc.' -> 'ROGERS COMMUNICATIONS'
    """
    upper = payee.upper()
    # Strip common legal suffixes
    upper = re.sub(
        r"\b(INC\.?|LTD\.?|CORP\.?|CO\.?|LLC\.?|INCORPORATED|LIMITED|CORPORATION)\b",
        "", upper
    )
    # Collapse whitespace
    words = upper.split()
    # Take up to 2 significant words (skip very short words like '&', 'THE')
    sig = [w for w in words if len(w) >= 3][:2]
    return " ".join(sig).strip().rstrip(".,")


# ---------------------------------------------------------------------------
# Gmail search
# ---------------------------------------------------------------------------

_GMAIL_QUERIES = [
    "has:attachment filename:pdf label:vtx-processed",
    "is:unread has:attachment filename:pdf in:inbox",
]


def _fetch_pdf_messages(notifier: GmailNotifier) -> list[dict]:
    seen: set[str] = set()
    msgs: list[dict] = []
    for q in _GMAIL_QUERIES:
        for m in notifier.poll_for_pdf_attachments(query=q, max_results=50):
            if m["msg_id"] not in seen:
                seen.add(m["msg_id"])
                msgs.append(m)
    return msgs


# ---------------------------------------------------------------------------
# Core: extract payees from one PDF
# ---------------------------------------------------------------------------

def _extract_from_pdf(pdf_path: Path) -> dict[str, object]:
    """Return {cheque_no: ChequeInfo} for all cheques found in pdf_path."""
    from sage50.cheque_extractor import _is_cheque_page
    ext = BankStatementExtractor().extract(pdf_path)
    n_page_texts = len(ext.page_texts)
    print(
        f"      {ext.path_used.value}  conf={ext.confidence:.2f}  "
        f"page_texts={n_page_texts}  {ext.elapsed_ms}ms"
    )
    if not ext.page_texts:
        print("      [warn] no per-page texts — cheque extraction skipped")
        return {}
    # Diagnostic: show what each page looks like
    for i, pt in enumerate(ext.page_texts):
        first = pt.strip()[:80].replace("\n", " | ")
        is_chq = _is_cheque_page(pt)
        print(f"        page {i}: is_cheque={is_chq}  [{first}]")
    cheque_map = extract_cheque_map(ext.page_texts)
    return cheque_map


# ---------------------------------------------------------------------------
# Deduplicated payee aggregation
# ---------------------------------------------------------------------------

def _aggregate_payees(
    all_maps: list[dict],
) -> dict[str, tuple[int, str, list[str]]]:
    """Merge payee maps from multiple PDFs into {keyword: (gl_no, gl_name, [chq_nos])}.

    Deduplicates by normalised keyword so the same payee across months
    produces exactly one rule entry.
    """
    result: dict[str, tuple[int, str, list[str]]] = {}
    for cmap in all_maps:
        for chq_no, info in cmap.items():
            if not info.payee:
                continue
            kw = _keyword_for(info.payee)
            if not kw:
                continue
            if kw not in result:
                gl_no, gl_name = _guess_gl(kw)
                result[kw] = (gl_no, gl_name, [])
            if chq_no not in result[kw][2]:
                result[kw][2].append(chq_no)
    return result


# ---------------------------------------------------------------------------
# Patch categorization_rules.py
# ---------------------------------------------------------------------------

def _load_existing_payees(src: str) -> set[str]:
    """Return the set of keywords already in _CHEQUE_PAYEES."""
    existing: set[str] = set()
    m = re.search(r"_CHEQUE_PAYEES\s*:\s*list\[.*?\]\s*=\s*\[(.*?)\]", src, re.DOTALL)
    if not m:
        return existing
    for line in m.group(1).splitlines():
        km = re.search(r'"\s*([^"]+)\s*"', line)
        if km:
            existing.add(km.group(1).strip().upper())
    return existing


def _patch_rules_file(
    new_payees: dict[str, tuple[int, str, list[str]]],
    dry_run: bool,
) -> int:
    """Add new payee entries to _CHEQUE_PAYEES. Returns count of entries added."""
    src = RULES_FILE.read_text(encoding="utf-8")
    existing = _load_existing_payees(src)

    to_add = [
        (kw, gl_no, gl_name, chqs)
        for kw, (gl_no, gl_name, chqs) in sorted(new_payees.items())
        if kw.upper() not in existing
    ]

    if not to_add:
        print("\n_CHEQUE_PAYEES: no new entries to add (all already present).")
        return 0

    # Build the replacement block
    new_entries = "\n".join(
        f'    ("{kw}", {gl_no}, "{gl_name}"),  # {", ".join(sorted(chqs))}'
        for kw, gl_no, gl_name, chqs in to_add
    )

    # Find the existing _CHEQUE_PAYEES list and replace it
    pattern = re.compile(
        r"(_CHEQUE_PAYEES\s*:\s*list\[.*?\]\s*=\s*\[)(.*?)(\])",
        re.DOTALL,
    )

    def replacer(m: re.Match) -> str:
        existing_body = m.group(2).rstrip()
        if existing_body.strip():
            body = existing_body + "\n" + new_entries + "\n"
        else:
            body = "\n" + new_entries + "\n"
        return m.group(1) + body + m.group(3)

    new_src = pattern.sub(replacer, src)

    if new_src == src:
        print("\n[warn] Could not locate _CHEQUE_PAYEES block to patch.")
        return 0

    if not dry_run:
        RULES_FILE.write_text(new_src, encoding="utf-8")
        print(f"\nPatched {RULES_FILE.name}: {len(to_add)} new entr{'y' if len(to_add)==1 else 'ies'} added.")
    else:
        print(f"\n[dry-run] Would add {len(to_add)} new entr{'y' if len(to_add)==1 else 'ies'}.")

    return len(to_add)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Extract cheque payees and patch categorization rules")
    parser.add_argument("--dry-run", action="store_true", help="Report only; do not modify categorization_rules.py")
    args = parser.parse_args()

    print("=== extract_cheque_payees ===\n")
    print("Connecting to Gmail...")
    notifier = GmailNotifier()
    profile  = notifier.get_profile()
    print(f"  Inbox: {profile.get('emailAddress')}  ({profile.get('messagesTotal', '?')} messages total)\n")

    print("Searching for bank statement PDFs (vtx-processed + unread)...")
    msgs = _fetch_pdf_messages(notifier)
    print(f"  Found {len(msgs)} message(s) with PDF attachments.\n")

    if not msgs:
        print("Nothing to process. Exiting.")
        return

    all_maps: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="vtx_chq_") as tmp:
        tmp_path = Path(tmp)
        for msg in msgs:
            print(f"  [{msg['subject'] or '(no subject)'}]  from {msg['from']}")
            for att in msg["attachments"]:
                print(f"    {att['filename']}  ({att['size']:,} bytes)")
                try:
                    pdf_path = notifier.save_attachment(
                        msg["msg_id"], att["attachment_id"], att["filename"], tmp_path
                    )
                    cmap = _extract_from_pdf(pdf_path)
                    if cmap:
                        print(f"      {len(cmap)} cheque(s) extracted: {', '.join(sorted(cmap))}")
                        all_maps.append(cmap)
                    else:
                        print("      0 cheques found on cheque pages.")
                except Exception as exc:
                    print(f"      [error] {exc}")
            print()

    # Aggregate
    payees = _aggregate_payees(all_maps)

    # Report
    print(f"\n{'=' * 60}")
    print(f"  {len(payees)} unique payee keyword(s) found across all statements\n")
    for kw, (gl_no, gl_name, chqs) in sorted(payees.items()):
        print(f"  {kw:<35}  GL {gl_no}  {gl_name}")
        print(f"    cheques: {', '.join(sorted(chqs))}")
    print(f"{'=' * 60}\n")

    # Patch
    _patch_rules_file(payees, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
