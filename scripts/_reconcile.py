"""
scripts/_reconcile.py  (one-off helper)
Reconcile each statement CSV against its own balance column (ground truth) and
classify every discrepancy:

  SIGN_FLIP : a single row whose sign, when flipped, makes the segment balance.
              -> high-confidence auto-fix (correct amount = balance-implied).
  GAP       : a segment whose sum disagrees with the balance delta and that no
              single sign flip reconciles -> a missing/garbled row; needs the PDF.

Segments run between consecutive rows that carry a balance; rows with a missing
(None) balance are absorbed into the spanning segment. Emits a machine-readable
corrections file (date,description,old_amount,new_amount) for the BQ fix step.

    python scripts/_reconcile.py --dir "R:\\...\\drop" --out "R:\\...\\drop\\_corrections.csv"
"""
from __future__ import annotations

import argparse
import csv
from decimal import Decimal
from pathlib import Path

TOL = Decimal("0.01")


def _amt(r) -> Decimal:
    deb = Decimal(r["Debit"]) if r.get("Debit") else Decimal(0)
    cred = Decimal(r["Credit"]) if r.get("Credit") else Decimal(0)
    return cred - deb


def _bal(r):
    return Decimal(r["Balance"]) if r.get("Balance") else None


def reconcile_csv(path: Path):
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    amounts = [_amt(r) for r in rows]
    bals = [_bal(r) for r in rows]
    # indices that carry a balance; anchor a virtual opening before row 0
    known = [i for i, b in enumerate(bals) if b is not None]
    fixes, gaps = [], []
    for a, b in zip(known, known[1:]):
        seg = list(range(a + 1, b + 1))          # rows whose sum moves bal[a]->bal[b]
        expected = bals[b] - bals[a]
        got = sum((amounts[i] for i in seg), Decimal(0))
        if abs(got - expected) <= TOL:
            continue
        # try a single-row sign flip that reconciles the segment
        flipped = None
        for i in seg:
            if abs((got - 2 * amounts[i]) - expected) <= TOL:
                flipped = i
                break
        if flipped is not None:
            new = -amounts[flipped]
            fixes.append({
                "file": path.name,
                "date": rows[flipped]["Date"],
                "description": rows[flipped]["Description"],
                "old_amount": str(amounts[flipped]),
                "new_amount": str(new),
                "type": "SIGN_FLIP",
            })
        else:
            gaps.append({
                "file": path.name,
                "from_date": rows[seg[0]]["Date"],
                "to_date": rows[seg[-1]]["Date"],
                "rows": len(seg),
                "parsed_sum": str(got),
                "balance_delta": str(expected),
                "diff": str(expected - got),
                "descriptions": " | ".join(rows[i]["Description"][:28] for i in seg),
            })
    return fixes, gaps


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--out", default=None, help="auto-fixable SIGN_FLIP corrections CSV")
    ap.add_argument("--review-out", default=None, help="full fill-in review worksheet CSV")
    args = ap.parse_args()

    d = Path(args.dir)
    all_fixes, all_gaps = [], []
    for f in sorted(d.glob("*.csv")):
        if f.name.startswith("_"):
            continue
        fx, gp = reconcile_csv(f)
        all_fixes += fx
        all_gaps += gp

    print(f"{'='*72}\nSIGN_FLIP corrections (balance-proven, auto-fixable): {len(all_fixes)}\n{'='*72}")
    for x in all_fixes:
        print(f"  {x['date']}  {x['old_amount']:>11} -> {x['new_amount']:>11}  "
              f"{x['description'][:42]}  [{x['file']}]")

    print(f"\n{'='*72}\nGAP segments (need PDF review, NOT auto-fixed): {len(all_gaps)}\n{'='*72}")
    for g in all_gaps:
        print(f"  {g['from_date']}..{g['to_date']} ({g['rows']} row[s])  "
              f"parsed={g['parsed_sum']} vs balance_delta={g['balance_delta']} "
              f"(off {g['diff']})  [{g['file']}]")
        print(f"      {g['descriptions']}")

    if args.out:
        with open(args.out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["file", "date", "description",
                                               "old_amount", "new_amount", "type"])
            w.writeheader()
            w.writerows(all_fixes)
        print(f"\nWrote {len(all_fixes)} auto-corrections -> {args.out}")

    if args.review_out:
        import re as _re

        def _period(fn):
            m = _re.search(r"(20\d\d-\d\d)", fn)
            return m.group(1) if m else fn

        with open(args.review_out, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["statement", "date", "description", "parsed_amount",
                        "balance_says", "difference", "type",
                        "CORRECT_AMOUNT (fill in)", "NOTES"])
            for x in all_fixes:
                w.writerow([_period(x["file"]), x["date"], x["description"],
                            x["old_amount"], x["new_amount"], "", "SIGN_FLIP",
                            x["new_amount"], "balance-proven sign flip (suggested)"])
            for g in all_gaps:
                w.writerow([_period(g["file"]),
                            g["from_date"] if g["from_date"] == g["to_date"]
                            else f"{g['from_date']}..{g['to_date']}",
                            g["descriptions"], g["parsed_sum"], g["balance_delta"],
                            g["diff"], "GAP", "", "check PDF: missing/garbled txn"])
        print(f"Wrote {len(all_fixes) + len(all_gaps)} review rows -> {args.review_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
