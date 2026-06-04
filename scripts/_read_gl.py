"""
scripts/_read_gl.py  (one-off helper)
Read a Sage 50 company's General Ledger via Sage50Bridge and summarize how
bank-paid transactions were categorized, so we can build a per-client
categorization ruleset.

    python scripts/_read_gl.py --sai "R:\\Canadian Federation of theotherapy\\2024.SAI" \
        --start 2024-01-01 --end 2024-12-31 --out R:\\...\\gl-2024.csv

Sage 50 must be CLOSED (the bridge opens the .SAI exclusively).
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _d(s: str | None):
    return date.fromisoformat(s) if s else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sai", required=True)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--out", default=None, help="optional CSV dump path")
    ap.add_argument("--source", default=None, help="filter to a source code, e.g. BNK")
    args = ap.parse_args()

    from sage50.bridge_reader import fetch_gl_transactions

    txns = fetch_gl_transactions(_d(args.start), _d(args.end), sai_file=args.sai)
    print(f"Fetched {len(txns)} GL lines from {args.sai}")
    if not txns:
        return 1

    sources = defaultdict(int)
    for t in txns:
        sources[t.source or ""] += 1
    print("Sources:", dict(sorted(sources.items(), key=lambda kv: -kv[1])))

    if args.source:
        txns = [t for t in txns if (t.source or "").upper() == args.source.upper()]
        print(f"Filtered to source={args.source!r}: {len(txns)} lines")

    # Group by account -> sample descriptions
    by_acct: dict[tuple[str, str], list] = defaultdict(list)
    for t in txns:
        by_acct[(t.account_no, t.account_name)].append(t)

    print(f"\n{'='*70}\nGL accounts used ({len(by_acct)}):\n{'='*70}")
    for (acct, name), rows in sorted(by_acct.items(), key=lambda kv: -len(kv[1])):
        descs = defaultdict(int)
        for r in rows:
            d = (r.description or "").strip()
            if d:
                descs[d] += 1
        top = sorted(descs.items(), key=lambda kv: -kv[1])[:8]
        print(f"\n[{acct}] {name}  — {len(rows)} lines")
        for d, n in top:
            print(f"      {n:>3}x  {d[:70]}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["Date", "Journal", "Source", "Account No", "Account Name",
                        "Debit", "Credit", "Description"])
            for t in txns:
                w.writerow([
                    t.transaction_date.isoformat() if t.transaction_date else "",
                    t.journal_no, t.source, t.account_no, t.account_name,
                    str(t.debit) if t.debit else "", str(t.credit) if t.credit else "",
                    t.description,
                ])
        print(f"\nWrote {len(txns)} lines -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
