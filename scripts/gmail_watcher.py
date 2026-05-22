"""
scripts/gmail_watcher.py
VTX-OS Gmail bank statement watcher.

For each unread inbox email with a PDF attachment:
  1. Download the PDF via Gmail API
  2. OCR the PDF with Document AI  →  plain text
  3. Parse OCR text                →  CSV (Date, Description, Debit, Credit, Balance)
  4. Drop CSV to R:\\<client_folder>\\drop\\   (manual pickup / csv_watcher integration)
  5. Upload CSV to GCS:  sage50/raw/YYYY/MM/DD/bank_statement/<filename>
  6. Trigger BookkeepingAgent directly (parse → categorize → BQ → approval queue)
  7. Mark email as read and label it vtx-processed

Usage:
    python scripts/gmail_watcher.py --client concetta --once
    python scripts/gmail_watcher.py --client concetta --period 2026-02 --dry-run
    python scripts/gmail_watcher.py --client concetta --interval 300

    --client   required  client slug from the registry below (e.g. concetta)
    --period   optional  override period detection, e.g. 2026-02
    --once               process current unread batch then exit
    --interval           poll interval in seconds (default: 300)
    --dry-run            OCR + parse only; skip R:\\, GCS, and BookkeepingAgent
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
# Client registry — extend as new clients are onboarded
# ---------------------------------------------------------------------------

_R_DRIVE    = Path(r"R:\\")
GCS_BUCKET  = "vtx-accounting-os-prod-vtx-exports"
GCP_PROJECT = "vtx-accounting-os-prod"
_DEFAULT_INTERVAL = 300  # seconds

_CLIENT_CONFIGS: dict[str, dict] = {
    "concetta": {
        "r_folder":        "Concetta Enterprises Inc",
        "account_no":      "xxxx5443",
        "gl_bank_account": "1060",
        "client_id":       "concetta",
    },
}


def _resolve_client(slug: str) -> dict:
    cfg = _CLIENT_CONFIGS.get(slug.lower())
    if cfg is None:
        known = ", ".join(_CLIENT_CONFIGS)
        raise SystemExit(f"Unknown --client '{slug}'. Known clients: {known}")
    return cfg


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


def _period_from_filename(name: str) -> str | None:
    """Try to extract YYYY-MM from a filename like 'dec-2025-bank.pdf'."""
    m = re.search(r"(\d{4})[-_](\d{2})", name)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.search(
        r"(january|february|march|april|may|june|july|august|september"
        r"|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
        r"[-_ ]?(\d{4})",
        name, re.I,
    )
    if m:
        return f"{m.group(2)}-{_MONTH_ABBR[m.group(1).lower()]}"
    return None


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


# ---------------------------------------------------------------------------
# R:\ drop folder
# ---------------------------------------------------------------------------

def _write_to_drop(csv_path: Path, cfg: dict) -> Path | None:
    """Copy CSV to R:\\<r_folder>\\drop\\. Returns destination path or None on failure."""
    drop_dir = _R_DRIVE / cfg["r_folder"] / "drop"
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

def _run_bookkeeping(csv_path: Path, period: str, cfg: dict) -> dict:
    from agents.bookkeeping import BookkeepingAgent
    from agents.base import TaskRequest, TaskType

    result = BookkeepingAgent().run(TaskRequest(
        task_type=TaskType.BOOKKEEPING_RUN,
        payload={
            "csv_path":        str(csv_path),
            "account_no":      cfg["account_no"],
            "gl_bank_account": cfg["gl_bank_account"],
            "period":          period,
            "client_id":       cfg["client_id"],
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
    period_override: str | None,
    cfg: dict,
    dry_run: bool,
) -> dict:
    from core.docai_ocr import ocr_pdf_bytes
    from sage50.bank_statement_ocr_parser import detect_bank, parse_and_write_csv

    period = period_override or _period_from_filename(fname) or _period_from_epoch(epoch_ms)
    print(f"    Period : {period}")

    # Step 1 — Document AI OCR
    print(f"    OCR    : Document AI...", end=" ", flush=True)
    ocr_text = ocr_pdf_bytes(pdf_path.read_bytes())
    print(f"{len(ocr_text):,} chars")

    if not ocr_text.strip():
        return {"error": "Document AI returned empty text — check processor ID and PDF quality"}

    # Step 2 — Parse OCR text → CSV
    bank     = detect_bank(ocr_text)
    safe     = re.sub(r"[^\w.\-]", "_", fname).replace(".pdf", f"-{period}.csv")
    csv_path = pdf_path.parent / safe

    n = parse_and_write_csv(ocr_text, csv_path, bank=bank)
    print(f"    Bank   : {bank.value}  |  parsed {n} transactions → {safe}")

    if n == 0:
        return {"error": "zero transactions parsed — OCR text may not contain a statement table"}

    result: dict = {"period": period, "bank": bank.value, "transactions": n}

    if dry_run:
        print(f"    [dry-run] skipping R:\\, GCS, BookkeepingAgent")
        result["csv"] = str(csv_path)
        return result

    # Step 3 — R:\ drop folder
    r_dest = _write_to_drop(csv_path, cfg)
    if r_dest:
        print(f"    R:\\    : {r_dest}")
        result["r_dest"] = str(r_dest)

    # Step 4 — GCS upload
    try:
        gcs_uri = _upload_to_gcs(csv_path, period)
        print(f"    GCS    : {gcs_uri}")
        result["gcs_uri"] = gcs_uri
    except Exception as exc:
        print(f"    [warn] GCS upload failed: {exc}")

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
# Core: process one email message
# ---------------------------------------------------------------------------

def _process_message(
    notifier,
    msg: dict,
    period_override: str | None,
    cfg: dict,
    dry_run: bool,
) -> None:
    print(f"\n{'─' * 60}")
    print(f"  From   : {msg['from']}")
    print(f"  Subject: {msg['subject']}")
    print(f"  PDFs   : {[a['filename'] for a in msg['attachments']]}")

    with tempfile.TemporaryDirectory(prefix="vtx_") as tmp:
        tmp_path = Path(tmp)
        for att in msg["attachments"]:
            print(f"\n  [{att['filename']}]  {att['size']:,} bytes")
            try:
                pdf_path = notifier.save_attachment(
                    msg["msg_id"], att["attachment_id"], att["filename"], tmp_path
                )
                _process_pdf(
                    pdf_path, att["filename"], msg["epoch_ms"],
                    period_override, cfg, dry_run,
                )
            except Exception as exc:
                print(f"  ERROR: {att['filename']}: {exc}")

    if not dry_run:
        notifier.mark_read(msg["msg_id"])
        print(f"\n  Marked read + labelled vtx-processed.")


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

def poll_once(notifier, period_override: str | None, cfg: dict, dry_run: bool) -> int:
    msgs = notifier.poll_for_pdf_attachments()
    if not msgs:
        print("  No unread bank statement emails.")
        return 0
    print(f"  Found {len(msgs)} message(s) with PDF attachments.")
    for msg in msgs:
        try:
            _process_message(notifier, msg, period_override, cfg, dry_run)
        except Exception as exc:
            print(f"  ERROR on {msg.get('msg_id', '?')}: {exc}")
    return len(msgs)


def run_daemon(
    client_slug: str,
    period: str | None,
    interval: int,
    once: bool,
    dry_run: bool,
) -> None:
    from core.gmail_notifier import GmailNotifier

    cfg      = _resolve_client(client_slug)
    notifier = GmailNotifier()

    print("VTX-OS Gmail Watcher")
    print(f"  Client  : {client_slug}  ({cfg['r_folder']})")
    print(f"  Period  : {period or 'auto-detect from filename / email date'}")
    print(f"  Mode    : {'once' if once else f'daemon, poll every {interval}s'}")
    print(f"  Dry run : {dry_run}\n")

    while True:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking inbox...")
        poll_once(notifier, period, cfg, dry_run)
        if once:
            break
        print(f"  Sleeping {interval}s...")
        time.sleep(interval)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VTX-OS Gmail bank statement watcher")
    parser.add_argument("--client",   required=True,
                        help="Client slug, e.g. concetta")
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
