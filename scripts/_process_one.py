"""
scripts/_process_one.py  (one-off helper)
Process a SINGLE inbox statement, matched by a subject substring, instead of
the whole unread batch. Reuses gmail_watcher._process_message so routing,
period detection, and booking are byte-for-byte identical to the daemon.

    python scripts/_process_one.py --match "Canadian Federation" --period 2025-01 --dry-run
    python scripts/_process_one.py --match "Canadian Federation" --period 2025-01

--match    case-insensitive substring matched against the email Subject
--period   optional period override (YYYY-MM); else auto-detected
--dry-run  OCR + parse + route only; skip R:\\, GCS, BookkeepingAgent, mark-read
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--match", required=True, help="subject substring (case-insensitive)")
    ap.add_argument("--period", default=None, help="period override YYYY-MM")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from core.gmail_notifier import GmailNotifier
    from core.client_registry import load_registry
    from scripts.gmail_watcher import _process_message

    registry = load_registry()
    print(f"Registry: {len(registry)} client account(s)")

    notifier = GmailNotifier()
    msgs = notifier.poll_for_pdf_attachments()
    print(f"Inbox: {len(msgs)} message(s) with PDF attachments.")

    needle = args.match.lower()
    hits = [m for m in msgs if needle in (m.get("subject", "").lower())]
    if not hits:
        print(f"No unread message whose subject contains {args.match!r}.")
        print("Subjects seen:")
        for m in msgs:
            print(f"  - {m.get('subject', '')!r}")
        return 1
    if len(hits) > 1:
        print(f"{len(hits)} messages match {args.match!r}; processing all of them:")

    for msg in hits:
        # pinned=None -> auto-route by the account on the statement (registry-driven)
        _process_message(notifier, msg, args.period, registry, None, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
