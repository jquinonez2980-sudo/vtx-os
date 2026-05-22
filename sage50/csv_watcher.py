"""
csv_watcher.py — Sage 50 CSV drop-folder watcher (multi-client).

Reads client list from the Bookkeeping network drive (R:\\) and creates
a matching drop-folder structure under WATCH_DIR.

Drop-folder layout:
    C:\\sage50_exports\\
        Del Plata Motors Inc\\
            gl_transactions\\
            ar_invoices\\
            ...
        706 Eaa Inc\\
            gl_transactions\\
            ...

GCS path:
    sage50/raw/YYYY/MM/DD/{client_slug}/{report_type}/{filename}

Usage:
    python csv_watcher.py                  # uses defaults
    python csv_watcher.py C:\\my_exports   # explicit watch dir

Setup (one-time):
    pip install watchdog google-cloud-storage
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from csv_uploader import ReportType, move_to_failed, upload_export, BUCKET

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_WATCH_DIR = Path(r"C:\sage50_exports")
BOOKKEEPING_DRIVE = Path(r"R:\\")
STABLE_WAIT_SECS  = 3
POLL_INTERVAL     = 1

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(
            Path(__file__).parent / "csv_watcher.log", encoding="utf-8"
        ),
    ],
)
log = logging.getLogger(__name__)

_FOLDER_TO_REPORT: dict[str, ReportType] = {rt.value: rt for rt in ReportType}

# Folders on R:\ that are NOT clients
_SKIP_FOLDERS = {"Trial Balance Template", "2024.SAJ"}


# ---------------------------------------------------------------------------
# Client helpers
# ---------------------------------------------------------------------------

def _slug(name: str) -> str:
    """Convert a client folder name to a safe GCS slug.
    'Del Plata Motors Inc'            → 'del_plata_motors_inc'
    '7120401 Canada Inc.- Frank\\'s Kitchen' → '7120401_canada_inc_franks_kitchen'
    """
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def load_clients(bookkeeping_drive: Path) -> dict[str, str]:
    """Return {folder_name: slug} for every client folder on the bookkeeping drive."""
    if not bookkeeping_drive.exists():
        log.warning("Bookkeeping drive not found: %s", bookkeeping_drive)
        return {}

    clients = {}
    for item in sorted(bookkeeping_drive.iterdir()):
        if item.is_dir() and not item.name.startswith("."):
            if item.name in _SKIP_FOLDERS:
                continue
            clients[item.name] = _slug(item.name)

    log.info("Loaded %d clients from %s", len(clients), bookkeeping_drive)
    return clients


# ---------------------------------------------------------------------------
# Drop-folder setup
# ---------------------------------------------------------------------------

def create_drop_folders(watch_dir: Path, clients: dict[str, str]) -> None:
    """Create watch_dir/ClientName/report_type/ for every client."""
    watch_dir.mkdir(parents=True, exist_ok=True)
    for client_name in clients:
        for rt in ReportType:
            (watch_dir / client_name / rt.value).mkdir(parents=True, exist_ok=True)
    log.info("Drop folders ready under %s", watch_dir)


# ---------------------------------------------------------------------------
# File processing
# ---------------------------------------------------------------------------

def _detect(csv_path: Path, watch_dir: Path, clients: dict[str, str]):
    """Return (client_name, client_slug, ReportType) or None."""
    try:
        rel = csv_path.relative_to(watch_dir)
    except ValueError:
        return None

    parts = rel.parts  # ('Del Plata Motors Inc', 'gl_transactions', 'file.csv')
    if len(parts) < 3:
        return None

    client_name   = parts[0]
    report_folder = parts[1]
    client_slug   = clients.get(client_name)
    report_type   = _FOLDER_TO_REPORT.get(report_folder)

    if not client_slug:
        log.warning("Unknown client folder '%s'", client_name)
        return None
    if not report_type:
        log.warning("Unknown report folder '%s'", report_folder)
        return None

    return client_name, client_slug, report_type


def _is_stable(path: Path, wait: float = STABLE_WAIT_SECS) -> bool:
    try:
        mtime = path.stat().st_mtime
        time.sleep(wait)
        return path.stat().st_mtime == mtime
    except FileNotFoundError:
        return False


def _archive_local(path: Path) -> None:
    done_dir = path.parent / ".done"
    done_dir.mkdir(exist_ok=True)
    dest = done_dir / path.name
    if dest.exists():
        dest = done_dir / f"{path.stem}_{int(time.time())}{path.suffix}"
    path.rename(dest)
    log.info("Archived locally → %s", dest)


def _upload_with_client(csv_path, report_type, client_slug, now):
    """Upload to GCS with client slug embedded in the blob path."""
    from google.cloud import storage as gcs
    import uuid

    date_prefix = now.strftime("%Y/%m/%d")
    blob_name   = f"sage50/raw/{date_prefix}/{client_slug}/{report_type.value}/{csv_path.name}"

    from csv_uploader import PROJECT
    client  = gcs.Client(project=PROJECT)
    bucket  = client.bucket(BUCKET)
    blob    = bucket.blob(blob_name)
    blob.metadata = {
        "report_type": report_type.value,
        "client":      client_slug,
        "export_date": now.isoformat(),
        "source_file": csv_path.name,
        "upload_id":   str(uuid.uuid4()),
    }
    blob.upload_from_filename(str(csv_path), content_type="text/csv")
    raw_uri = f"gs://{BUCKET}/{blob_name}"

    # copy to staging
    staging_name = blob_name.replace("sage50/raw/", "sage50/staging/", 1)
    bucket.copy_blob(blob, bucket, staging_name)

    return raw_uri


def process_file(csv_path: Path, watch_dir: Path, clients: dict[str, str]) -> None:
    if csv_path.suffix.lower() != ".csv":
        return
    if not csv_path.exists():
        return
    if ".done" in csv_path.parts:
        return

    detected = _detect(csv_path, watch_dir, clients)
    if detected is None:
        return

    client_name, client_slug, report_type = detected

    if not _is_stable(csv_path):
        log.info("File still being written: %s", csv_path.name)
        return

    log.info("Processing  %s  |  client=%s  |  report=%s",
             csv_path.name, client_name, report_type.value)

    gcs_uri = None
    try:
        now     = datetime.now(timezone.utc)
        gcs_uri = _upload_with_client(csv_path, report_type, client_slug, now)
        log.info("Uploaded → %s", gcs_uri)
        _archive_local(csv_path)
    except Exception as exc:
        log.error("Upload failed for %s: %s", csv_path.name, exc)
        if gcs_uri:
            try:
                move_to_failed(gcs_uri, str(exc))
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Watchdog handler
# ---------------------------------------------------------------------------

class CSVHandler(FileSystemEventHandler):
    def __init__(self, watch_dir: Path, clients: dict[str, str]):
        self.watch_dir = watch_dir
        self.clients   = clients

    def on_created(self, event) -> None:
        if not event.is_directory:
            process_file(Path(event.src_path), self.watch_dir, self.clients)

    def on_moved(self, event) -> None:
        if not event.is_directory:
            process_file(Path(event.dest_path), self.watch_dir, self.clients)


# ---------------------------------------------------------------------------
# Startup scan
# ---------------------------------------------------------------------------

def scan_existing(watch_dir: Path, clients: dict[str, str]) -> None:
    log.info("Scanning for existing CSV files ...")
    found = [f for f in watch_dir.rglob("*.csv") if ".done" not in f.parts]
    if not found:
        log.info("No existing files found.")
        return
    log.info("Found %d file(s) to process.", len(found))
    for f in found:
        process_file(f, watch_dir, clients)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    watch_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        os.environ.get("WATCH_DIR", str(DEFAULT_WATCH_DIR))
    )

    clients = load_clients(BOOKKEEPING_DRIVE)
    create_drop_folders(watch_dir, clients)

    log.info("=" * 60)
    log.info("Sage 50 CSV Watcher started  (multi-client)")
    log.info("Watching : %s", watch_dir)
    log.info("Clients  : %d loaded from %s", len(clients), BOOKKEEPING_DRIVE)
    log.info("Bucket   : %s", BUCKET)
    log.info("=" * 60)

    scan_existing(watch_dir, clients)

    handler  = CSVHandler(watch_dir, clients)
    observer = Observer()
    observer.schedule(handler, str(watch_dir), recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        log.info("Shutting down.")
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()