"""
scripts/_undo_reversals.py  (one-off correction helper)
Neutralize a SPECIFIC journal-id range of 'REV:'-tagged reversal entries by
posting exact mirrors ('UND:'-tagged).

Context (2026-06-10, theotherapy 2025.SAI): the books were correct up to
journal 1961 (originals + REV set A + manual fixes). REV set B (journals
1962-2356) was posted by a second _fix_gl_bank run and must be undone —
and ONLY it, because the manual fixes were built around set A's presence.

    # dry-run with full per-journal listing (Sage must be CLOSED):
    python scripts/_undo_reversals.py --sai "R:\\...\\2025.SAI" --jid-from 1962 --jid-to 2356
    # post the mirrors:
    python scripts/_undo_reversals.py --sai "..." --jid-from 1962 --jid-to 2356 --commit
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sai", required=True)
    ap.add_argument("--user", default="sysadmin")
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--jid-from", type=int, required=True,
                    help="first journal id of the REV: set to neutralize")
    ap.add_argument("--jid-to", type=int, required=True,
                    help="last journal id of the REV: set to neutralize")
    ap.add_argument("--expect", type=int, default=395,
                    help="abort unless exactly this many journals are found (safety)")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    from sage50.bridge_reader import _get_creds, _run_bridge

    sai, usr, pwd = _get_creds(args.sai, args.user, None)
    rows = _run_bridge(sai, usr, pwd, "gl",
                       date(args.year, 1, 1), date(args.year, 12, 31))

    def desc(r) -> str:
        return str(r.get("szComment") or r.get("hdrComment") or "")

    def jid(r) -> int:
        return int(r.get("lJEntID") or 0)

    def amt(r) -> Decimal:
        return Decimal(str(r.get("dAmount") or 0))

    def true_dr_cr(r) -> tuple[Decimal, Decimal]:
        """The bridge GL report signs dAmount by BALANCE IMPACT, not debit/credit:
        on a debit-natural account (1xxx assets, 5xxx expenses) a debit is +;
        on a credit-natural account (2xxx liab, 3xxx equity, 4xxx revenue) a
        CREDIT is + and a debit is −. Decode to true (debit, credit)."""
        a = amt(r)
        first = str(r.get("lAcctId"))[0]          # lId "40200000" -> '4'
        credit_natural = first in ("2", "3", "4")
        if credit_natural:
            return (-a, Decimal("0")) if a < 0 else (Decimal("0"), a)
        return (a, Decimal("0")) if a > 0 else (Decimal("0"), -a)

    by_journal: dict[int, list] = defaultdict(list)
    for r in rows:
        if (args.jid_from <= jid(r) <= args.jid_to
                and desc(r).startswith("REV:")):
            by_journal[jid(r)].append(r)

    print(f"REV: journals in {args.jid_from}..{args.jid_to}: {len(by_journal)}")
    if len(by_journal) != args.expect:
        print(f"ABORT: expected exactly {args.expect} journals "
              f"(--expect to override). Found {len(by_journal)} — investigate first.")
        return 1

    entries, skipped = [], []
    for j in sorted(by_journal):
        lines = by_journal[j]
        decoded = [true_dr_cr(r) for r in lines]
        dr_total = sum(d for d, _ in decoded)
        cr_total = sum(c for _, c in decoded)
        if len(lines) != 2 or dr_total != cr_total:
            skipped.append((j, lines, dr_total - cr_total))
            continue
        und = ("UND:" + desc(lines[0])[4:])[:39]
        entries.append({
            "date": str(lines[0].get("txnDate")),
            "source": "BNK",
            "comment": und,
            "lines": [
                # exact mirror: the REV line's true debit becomes our credit
                {"account_id": str(l.get("lAcctId")),
                 "debit": float(cr), "credit": float(dr),
                 "comment": und}
                for l, (dr, cr) in zip(lines, decoded)
            ],
        })

    if skipped:
        print(f"\nSKIPPED {len(skipped)} journal(s) — RESOLVE BEFORE COMMIT:")
        for j, lines, total in skipped:
            print(f"  jid={j}  lines={len(lines)}  net={total:+,.2f}")
            for l in lines:
                print(f"      acct={l.get('lAcctId')}  {amt(l):+,.2f}  {desc(l)[:40]}")

    print(f"\n{len(entries)} UND: mirrors built. Full listing "
          f"(date | mirror Dr acct amount -> Cr acct | comment):")
    for e in entries:
        dr = next(l for l in e["lines"] if l["debit"] > 0)
        cr = next(l for l in e["lines"] if l["credit"] > 0)
        print(f"  {e['date']}  Dr {dr['account_id']} {dr['debit']:>10,.2f} -> "
              f"Cr {cr['account_id']}  {e['comment']}")

    # net effect summary — eyeball against the diagnostic's per-set figures
    net: dict[str, Decimal] = defaultdict(Decimal)
    for e in entries:
        for l in e["lines"]:
            net[l["account_id"]] += Decimal(str(l["debit"])) - Decimal(str(l["credit"]))
    print("\nNet effect of posting these mirrors (should negate REV set B):")
    for acct in sorted(net):
        if net[acct] != 0:
            print(f"  {acct}: {net[acct]:+,.2f}")

    if not args.commit:
        print(f"\n[dry-run] no Sage write. Re-run with --commit (Sage 50 CLOSED).")
        return 0
    if skipped:
        print("\nABORT: skipped journals present — not posting a partial undo.")
        return 1

    if not args.no_backup:
        import shutil
        from datetime import datetime
        sai_path = Path(args.sai)
        saj = sai_path.with_suffix(".SAJ")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        bdir = sai_path.parent / "vtx_backup" / f"{sai_path.stem}_{stamp}"
        bdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sai_path, bdir / sai_path.name)
        if saj.is_dir():
            # process.pid etc. are transient connection-manager files that can
            # vanish mid-copy; they are not part of the books.
            shutil.copytree(saj, bdir / saj.name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(
                                "process.pid", "*.lock", "*.tmp", "~*"))
        print(f"[backup] -> {bdir}")

    from sage50.bridge_reader import post_journal_entries
    res = post_journal_entries(entries, sai_file=args.sai, user=args.user)
    print(f"posted={res.get('posted')} total={res.get('total')} errors={res.get('errors')}")
    return 0 if res.get("errors", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
