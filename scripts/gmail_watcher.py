"""
scripts/gmail_watcher.py
VTX-OS Gmail bank statement watcher.

For each unread inbox email with a PDF attachment:
  1. Download the PDF via Gmail API
  2. Extract text via BankStatementExtractor (PyMuPDF → pdfplumber → Document AI)
  3. Resolve the client by the account number on the statement (core.client_registry)
  4. Parse text                    →  CSV (Date, Description, Debit, Credit, Balance)
  5. Drop CSV to R:\\<client_folder>\\drop\\   (manual pickup / csv_watcher integration)
  6. Upload CSV to GCS:  sage50/raw/YYYY/MM/DD/bank_statement/<filename>
  7. Trigger BookkeepingAgent directly (parse → categorize → BQ → approval queue)
  8. Mark email read + label vtx-processed; unrouted mail is labelled vtx-unrouted
     (left unread) and a Google Chat alert is sent.

Usage:
    python scripts/gmail_watcher.py --once                       # auto-route all clients
    python scripts/gmail_watcher.py --client concetta --once     # pin to one client
    python scripts/gmail_watcher.py --period 2026-02 --dry-run
    python scripts/gmail_watcher.py --interval 300

    --client   optional  pin to one client slug; omit to auto-route by account number
    --period   optional  override period detection, e.g. 2026-02
    --once               process current unread batch then exit
    --interval           poll interval in seconds (default: 300)
    --dry-run            OCR + parse only; skip R:\\, GCS, and BookkeepingAgent

Client registry: R:\\bookkeeping\\client_accounts.csv (see core/client_registry.py).
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

# ---------------------------------------------------------------------------
# Routing — clients are resolved per-statement from core.client_registry
# (CSV on R:). See core/client_registry.py.
# ---------------------------------------------------------------------------

_R_DRIVE    = Path(r"R:\\")
GCS_BUCKET  = "vtx-accounting-os-prod-vtx-exports"
GCP_PROJECT = "vtx-accounting-os-prod"
_DEFAULT_INTERVAL = 300  # seconds
_UNROUTED_LABEL = "vtx-unrouted"


def _pin_client(registry: dict, slug: str) -> list:
    """Return ALL registry entries whose client_id matches *slug* (for --client).

    Clients with multiple bank accounts (e.g. two BMO accounts) have several
    registry rows sharing the same client_id.  Returning all of them lets the
    routing check accept any of their account numbers rather than just the first.
    """
    matches = [cfg for cfg in registry.values() if cfg.client_id.lower() == slug.lower()]
    if matches:
        return matches
    known = ", ".join(sorted({c.client_id for c in registry.values()})) or "(none)"
    raise SystemExit(f"Unknown --client '{slug}'. Known clients: {known}")


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------

_MONTH_ABBR = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    "january": "01",  "february": "02", "march": "03",  "april": "04",
    "june": "06",     "july": "07",     "august": "08", "september": "09",
    "october": "10",  "november": "11", "december": "12",
}


_MONTH_NAMES = (
    r"january|february|march|april|may|june|july|august|september"
    r"|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec"
)


def _period_from_filename(name: str) -> str | None:
    """Try to extract YYYY-MM from a filename or subject.

    Handles 'dec-2025-bank.pdf', 'February 2026', and the 'ending <Month> <DD>
    <YYYY>' form used in these subjects (e.g. 'ending February 07 2025').
    """
    m = re.search(r"(\d{4})[-_](\d{2})", name)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{m.group(2)}"
    # "Month DD YYYY" / "Month DD, YYYY" — a day sits between month and year.
    m = re.search(rf"({_MONTH_NAMES})\s+\d{{1,2}},?\s+(\d{{4}})", name, re.I)
    if m:
        return f"{m.group(2)}-{_MONTH_ABBR[m.group(1).lower()]}"
    # "Month YYYY" (no day)
    m = re.search(rf"({_MONTH_NAMES})[-_ ]?(\d{{4}})", name, re.I)
    if m:
        return f"{m.group(2)}-{_MONTH_ABBR[m.group(1).lower()]}"
    return None


# TD prints the statement period as a 'From - To' range, e.g.
# "DEC 31/25 - JAN 30/26". The period is the CLOSING (To) month/year.
_PERIOD_RANGE_RE = re.compile(
    r"[A-Za-z]{3}\s*\d{1,2}\s*/\s*\d{2}\s*[-–—]\s*([A-Za-z]{3})\s*\d{1,2}\s*/\s*(\d{2})"
)


def _period_from_text(text: str) -> str | None:
    """Parse the statement's own closing period from OCR text (YYYY-MM).

    Authoritative: reads the 'Statement From - To' range and returns the To
    (closing) date's month/year. Preferred over filename/subject/email-date,
    which are only heuristics.
    """
    m = _PERIOD_RANGE_RE.search(text)
    if not m:
        return None
    mon = _MONTH_ABBR.get(m.group(1).lower())
    return f"20{m.group(2)}-{mon}" if mon else None


def _period_from_subject(subject: str) -> str | None:
    """Parse YYYY-MM from an email subject like 'Bank statement February 2026'."""
    return _period_from_filename(subject)


def _period_from_epoch(epoch_ms: int) -> str:
    """Infer period from email date — statements arrive after their period closes."""
    d     = date.fromtimestamp(epoch_ms / 1000)
    month = d.month - 1 or 12
    year  = d.year if d.month > 1 else d.year - 1
    return f"{year}-{month:02d}"


# ---------------------------------------------------------------------------
# GCS upload
# ---------------------------------------------------------------------------

def _upload_to_gcs(csv_path: Path, period: str) -> str:
    from google.cloud import storage as gcs
    now       = datetime.now(timezone.utc)
    blob_name = f"sage50/raw/{now.strftime('%Y/%m/%d')}/bank_statement/{csv_path.name}"
    client    = gcs.Client(project=GCP_PROJECT)
    blob      = client.bucket(GCS_BUCKET).blob(blob_name)
    blob.metadata = {
        "period": period, "source": "gmail_watcher", "timestamp": now.isoformat()
    }
    blob.upload_from_filename(str(csv_path), content_type="text/csv")
    return f"gs://{GCS_BUCKET}/{blob_name}"


def _archive_pdf_to_gcs(pdf_path: Path, period: str, client_id: str) -> str:
    """Archive original PDF to GCS so cheque images are preserved for re-processing."""
    from google.cloud import storage as gcs
    now       = datetime.now(timezone.utc)
    blob_name = f"bank-statements/pdf/{now.strftime('%Y/%m/%d')}/{client_id}/{pdf_path.name}"
    client    = gcs.Client(project=GCP_PROJECT)
    blob      = client.bucket(GCS_BUCKET).blob(blob_name)
    blob.metadata = {
        "period": period, "client_id": client_id,
        "source": "gmail_watcher", "timestamp": now.isoformat(),
    }
    blob.upload_from_filename(str(pdf_path), content_type="application/pdf")
    return f"gs://{GCS_BUCKET}/{blob_name}"


# ---------------------------------------------------------------------------
# R:\ drop folder
# ---------------------------------------------------------------------------

def _write_to_drop(csv_path: Path, cfg) -> Path | None:
    """Copy CSV to R:\\<r_folder>\\drop\\. Returns destination path or None on failure."""
    drop_dir = _R_DRIVE / cfg.r_folder / "drop"
    try:
        drop_dir.mkdir(parents=True, exist_ok=True)
        dest = drop_dir / csv_path.name
        dest.write_bytes(csv_path.read_bytes())
        return dest
    except OSError as exc:
        print(f"    [warn] R:\\ drop write failed: {exc}")
        return None


# ---------------------------------------------------------------------------
# BookkeepingAgent trigger
# ---------------------------------------------------------------------------

def _run_bookkeeping(csv_path: Path, period: str, cfg) -> dict:
    from agents.bookkeeping import BookkeepingAgent
    from agents.base import TaskRequest, TaskType

    result = BookkeepingAgent().run(TaskRequest(
        task_type=TaskType.BOOKKEEPING_RUN,
        payload={
            "csv_path":        str(csv_path),
            "account_no":      cfg.account_masked,
            "gl_bank_account": cfg.gl_bank_account,
            "period":          period,
            "client_id":       cfg.client_id,
            "queue_reviews":   True,
            "notify_chat":     False,
        },
    ))
    return result.output if result.ok else {"error": result.error}


# ---------------------------------------------------------------------------
# Core: process one PDF
# ---------------------------------------------------------------------------

def _process_pdf(
    pdf_path: Path,
    fname: str,
    epoch_ms: int,
    subject: str,
    period_override: str | None,
    registry: dict,
    pinned,
    dry_run: bool,
) -> dict:
    from sage50.statement_extractor import BankStatementExtractor
    from sage50.bank_statement_ocr_parser import write_csv
    from core.client_registry import resolve, find_account_in_text

    # Step 1 — Extract text (PyMuPDF → pdfplumber → Document AI cascade).
    # Digital PDFs exit at PyMuPDF in ~50 ms; only scanned PDFs reach DocAI.
    print(f"    Extract: ", end=" ", flush=True)
    ext = BankStatementExtractor().extract(pdf_path)
    print(
        f"{ext.path_used.value} "
        f"(conf={ext.confidence:.2f}, {ext.pages}p, {ext.elapsed_ms} ms, {len(ext.text):,} chars)"
    )

    if not ext.text.strip():
        return {"error": "all extraction paths produced empty text — check PDF quality / DocAI processor"}

    # Period: prefer the statement's own closing date (authoritative), then
    # subject (human-written, most reliable for scanner-named files like
    # "Adobe Scan Jun 15, 2026.pdf" whose filename reflects the scan date),
    # then filename, then the email-arrival heuristic as last resort.
    period = (
        period_override
        or _period_from_text(ext.text)
        or _period_from_subject(subject)
        or _period_from_filename(fname)
        or _period_from_epoch(epoch_ms)
    )
    print(f"    Period : {period}")

    # Re-anchor transaction years to the (authoritative) period year. Guards
    # against _infer_year defaulting to the current year on a header with no
    # 4-digit year, which would silently mis-date the whole statement.
    from sage50.bank_statement_ocr_parser import anchor_year_to_period
    shift = anchor_year_to_period(ext.transactions, int(period[:4]))
    if shift:
        print(f"    [year] re-anchored to {period[:4]} (shift {shift:+d}y)")

    # Step 1b — Resolve which client this statement belongs to (route by any
    # registered account number printed on the statement; bank-agnostic). Never
    # book until resolved.
    matched = find_account_in_text(ext.text, registry)
    if pinned is not None:
        # pinned is a list of all configs for this client (one per bank account).
        # Refuse only if the statement positively matches a DIFFERENT client.
        # A non-match (matched is None) trusts the pin and uses the first config.
        if matched:
            cfg = next((c for c in pinned if c.account_no == matched), None)
            if cfg is None:
                accts = ", ".join(c.account_masked for c in pinned)
                print(f"    Client : UNROUTED — statement account {matched} ≠ pinned "
                      f"{pinned[0].client_id} (accounts: {accts})")
                return {"unrouted": True, "reason": "account mismatch vs pinned --client",
                        "parsed_account": matched, "fname": fname}
        else:
            cfg = pinned[0]
    else:
        cfg = resolve(ext.text, registry)
        if cfg is None:
            print(f"    Client : UNROUTED — no registered client matches this statement")
            return {"unrouted": True, "reason": "no client matches statement account",
                    "parsed_account": matched, "fname": fname}
    print(f"    Client : {cfg.r_folder}  ({cfg.account_masked})")

    # Step 2 — Write CSV (transactions already parsed during extraction)
    bank     = ext.bank_code
    safe     = re.sub(r"[^\w.\-]", "_", fname).replace(".pdf", f"-{period}.csv")
    csv_path = pdf_path.parent / safe

    n = write_csv(ext.transactions, csv_path)
    print(f"    Bank   : {bank.value}  |  parsed {n} transactions → {safe}")

    if n == 0:
        return {"error": "zero transactions parsed — statement layout not recognised"}

    result: dict = {
        "period": period, "bank": bank.value, "transactions": n,
        "extract_path": ext.path_used.value, "extract_ms": ext.elapsed_ms,
    }

    if dry_run:
        print(f"    [dry-run] skipping R:\\, GCS, BookkeepingAgent")
        result["csv"] = str(csv_path)
        return result

    # Step 3 — R:\ drop folder
    r_dest = _write_to_drop(csv_path, cfg)
    if r_dest:
        print(f"    R:\\    : {r_dest}")
        result["r_dest"] = str(r_dest)

    # Step 4 — GCS upload (CSV + original PDF archive)
    try:
        gcs_uri = _upload_to_gcs(csv_path, period)
        print(f"    GCS    : {gcs_uri}")
        result["gcs_uri"] = gcs_uri
    except Exception as exc:
        print(f"    [warn] GCS CSV upload failed: {exc}")

    try:
        pdf_gcs_uri = _archive_pdf_to_gcs(pdf_path, period, cfg.client_id)
        print(f"    PDF GCS: {pdf_gcs_uri}")
        result["pdf_gcs_uri"] = pdf_gcs_uri
    except Exception as exc:
        print(f"    [warn] PDF archive to GCS failed: {exc}")

    # Step 5 — BookkeepingAgent
    print(f"    Agent  : BookkeepingAgent...", end=" ", flush=True)
    bk = _run_bookkeeping(csv_path, period, cfg)
    if "error" in bk:
        print(f"FAILED — {bk['error']}")
    else:
        print(
            f"OK  total={bk.get('total_transactions', 0)}"
            f"  auto={bk.get('auto_categorized', 0)}"
            f"  review={bk.get('needs_review', 0)}"
        )
    result["bookkeeping"] = bk
    return result


# ---------------------------------------------------------------------------
# Core: process one CSV attachment (native bank export — no OCR needed)
# ---------------------------------------------------------------------------

def _process_csv(
    csv_path: Path,
    fname: str,
    epoch_ms: int,
    subject: str,
    period_override: str | None,
    registry: dict,
    pinned,
    dry_run: bool,
) -> dict:
    from sage50.bank_parser import parse_csv
    from core.client_registry import resolve, find_account_in_text

    print(f"    Format : CSV (native bank export — no OCR)", flush=True)

    try:
        txns = parse_csv(csv_path)
    except Exception as exc:
        return {"error": f"CSV parse failed: {exc}"}

    if not txns:
        return {"error": "zero transactions parsed — check CSV format/headers"}

    bank = txns[0].bank_code

    # Period: override → subject → filename → email-arrival heuristic
    period = (
        period_override
        or _period_from_subject(subject)
        or _period_from_filename(fname)
        or _period_from_epoch(epoch_ms)
    )
    print(f"    Period : {period}")

    # Routing — use account_no from first transaction if available
    acct_in_csv = txns[0].account_no if txns else None
    if pinned is not None:
        if acct_in_csv:
            cfg = next((c for c in pinned if c.account_no == acct_in_csv or
                        acct_in_csv.endswith(c.account_no.lstrip("x"))), None)
            if cfg is None:
                cfg = pinned[0]  # trust the pin — CSV may use masked account
        else:
            cfg = pinned[0]
    else:
        # Try to resolve by matching the raw text of the CSV
        csv_text = csv_path.read_text(errors="replace")
        cfg = resolve(csv_text, registry)
        if cfg is None:
            print(f"    Client : UNROUTED — no registered client matches this CSV")
            return {"unrouted": True, "reason": "no client matches CSV account",
                    "parsed_account": acct_in_csv, "fname": fname}

    print(f"    Client : {cfg.r_folder}  ({cfg.account_masked})")
    print(f"    Bank   : {bank.value}  |  parsed {len(txns)} transactions  (0 ms OCR)")

    result: dict = {
        "period": period, "bank": bank.value, "transactions": len(txns),
        "extract_path": "csv", "extract_ms": 0,
    }

    if dry_run:
        print(f"    [dry-run] skipping R:\\, GCS, BookkeepingAgent")
        result["csv"] = str(csv_path)
        return result

    # R:\ drop
    r_dest = _write_to_drop(csv_path, cfg)
    if r_dest:
        print(f"    R:\\    : {r_dest}")
        result["r_dest"] = str(r_dest)

    # GCS upload
    try:
        gcs_uri = _upload_to_gcs(csv_path, period)
        print(f"    GCS    : {gcs_uri}")
        result["gcs_uri"] = gcs_uri
    except Exception as exc:
        print(f"    [warn] GCS upload failed: {exc}")

    # BookkeepingAgent
    print(f"    Agent  : BookkeepingAgent...", end=" ", flush=True)
    bk = _run_bookkeeping(csv_path, period, cfg)
    if "error" in bk:
        print(f"FAILED — {bk['error']}")
    else:
        print(
            f"OK  total={bk.get('total_transactions', 0)}"
            f"  auto={bk.get('auto_categorized', 0)}"
            f"  review={bk.get('needs_review', 0)}"
        )
    result["bookkeeping"] = bk
    return result


# ---------------------------------------------------------------------------
# Core: process one email message
# ---------------------------------------------------------------------------

def _process_message(
    notifier,
    msg: dict,
    period_override: str | None,
    registry: dict,
    pinned,
    dry_run: bool,
) -> None:
    print(f"\n{'─' * 60}")
    print(f"  From   : {msg['from']}")
    print(f"  Subject: {msg['subject']}")
    print(f"  Files  : {[a['filename'] for a in msg['attachments']]}")

    results: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="vtx_") as tmp:
        tmp_path = Path(tmp)
        for att in msg["attachments"]:
            print(f"\n  [{att['filename']}]  {att['size']:,} bytes")
            try:
                file_path = notifier.save_attachment(
                    msg["msg_id"], att["attachment_id"], att["filename"], tmp_path
                )
                if att["filename"].lower().endswith(".csv"):
                    result = _process_csv(
                        file_path, att["filename"], msg["epoch_ms"],
                        msg.get("subject", ""), period_override, registry, pinned, dry_run,
                    )
                else:
                    result = _process_pdf(
                        file_path, att["filename"], msg["epoch_ms"],
                        msg.get("subject", ""), period_override, registry, pinned, dry_run,
                    )
                if "error" in result:
                    print(f"  ERROR: {att['filename']}: {result['error']}")
            except Exception as exc:
                result = {"error": str(exc), "fname": att["filename"]}
                print(f"  ERROR: {att['filename']}: {exc}")
            results.append(result)

    unrouted   = [r for r in results if r.get("unrouted")]
    all_booked = all("error" not in r and not r.get("unrouted") for r in results)

    if dry_run:
        if unrouted:
            print(f"\n  [dry-run] {len(unrouted)} attachment(s) UNROUTED — "
                  f"would label '{_UNROUTED_LABEL}' + send Chat alert.")
        return

    if unrouted:
        _quarantine(notifier, msg, unrouted)
    if all_booked:
        notifier.mark_read(msg["msg_id"])
        print(f"\n  Marked read + labelled vtx-processed.")
    else:
        print(f"\n  Email left unread for retry.")


def _quarantine(notifier, msg: dict, unrouted: list[dict]) -> None:
    """Label an unrouted email and alert via Google Chat (best-effort)."""
    from core.chat_notifier import send_alert

    try:
        notifier.apply_label(msg["msg_id"], _UNROUTED_LABEL)
        print(f"\n  Labelled '{_UNROUTED_LABEL}' (left unread for retry).")
    except Exception as exc:
        print(f"\n  [warn] could not apply '{_UNROUTED_LABEL}' label: {exc}")

    lines = [
        f"From: {msg['from']}",
        f"Subject: {msg['subject']}",
    ]
    for r in unrouted:
        lines.append(
            f"{r.get('fname', '?')}: account {r.get('parsed_account') or 'unreadable'} "
            f"— {r.get('reason', 'no client match')}"
        )
    lines.append("Add the account to R:\\bookkeeping\\client_accounts.csv to route it.")
    send_alert("VTX-OS: unrouted bank statement", lines)


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

def poll_once(notifier, period_override: str | None, registry: dict, pinned, dry_run: bool) -> int:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    msgs = notifier.poll_for_pdf_attachments()
    if not msgs:
        print("  No unread bank statement emails.")
        return 0
    print(f"  Found {len(msgs)} message(s) with PDF/CSV attachments.")

    # Process up to 4 messages concurrently. Each message writes to its own
    # temp dir and BQ stream so there is no shared mutable state.
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {
            pool.submit(
                _process_message, notifier, msg, period_override, registry, pinned, dry_run
            ): msg
            for msg in msgs
        }
        for fut in as_completed(futs):
            try:
                fut.result()
            except Exception as exc:
                msg = futs[fut]
                print(f"  ERROR on {msg.get('msg_id', '?')}: {exc}")
    return len(msgs)


def run_daemon(
    client_slug: str | None,
    period: str | None,
    interval: int,
    once: bool,
    dry_run: bool,
) -> None:
    from core.gmail_notifier import GmailNotifier
    from core.client_registry import load_registry, registry_path

    try:
        registry = load_registry()
    except FileNotFoundError as exc:
        raise SystemExit(str(exc))
    if not registry:
        raise SystemExit(f"Client registry is empty: {registry_path()}")

    pinned   = _pin_client(registry, client_slug) if client_slug else None
    notifier = GmailNotifier()

    print("VTX-OS Gmail Watcher")
    print(f"  Registry: {registry_path()}  ({len(registry)} client account(s))")
    if pinned is not None:
        accts = ", ".join(c.account_masked for c in pinned)
        print(f"  Client  : PINNED {pinned[0].client_id}  ({pinned[0].r_folder})  [{accts}]")
    else:
        print(f"  Client  : auto-route by statement account number")
    print(f"  Period  : {period or 'auto-detect from filename / email date'}")
    print(f"  Mode    : {'once' if once else f'daemon, poll every {interval}s'}")
    print(f"  Dry run : {dry_run}\n")

    while True:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking inbox...")
        poll_once(notifier, period, registry, pinned, dry_run)
        if once:
            break
        print(f"  Sleeping {interval}s...")
        time.sleep(interval)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VTX-OS Gmail bank statement watcher")
    parser.add_argument("--client",   default=None,
                        help="Optional: pin to one client slug (e.g. concetta). "
                             "Omit to auto-route every statement by its account number.")
    parser.add_argument("--period",   default=None,
                        help="Override period detection, e.g. 2026-02")
    parser.add_argument("--once",     action="store_true",
                        help="Process current unread batch then exit")
    parser.add_argument("--interval", type=int, default=_DEFAULT_INTERVAL,
                        help="Daemon poll interval in seconds (default: 300)")
    parser.add_argument("--dry-run",  action="store_true",
                        help="OCR + parse only; skip R:\\, GCS, BookkeepingAgent")
    args = parser.parse_args()

    run_daemon(
        client_slug=args.client,
        period=args.period,
        interval=args.interval,
        once=args.once,
        dry_run=args.dry_run,
    )
