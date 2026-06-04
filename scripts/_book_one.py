"""
scripts/_book_one.py  (one-off helper)
Preview or commit a booking from a saved statement CSV. Preview mode applies the
exact same parse -> min_date filter -> per-client categorization as
BookkeepingAgent, but writes NOTHING. --commit runs the real agent (BQ + queue).

    python scripts/_book_one.py --csv "...\\theotherapy-2025-01.csv" \
        --client theotherapy --period 2025-01 --bank BMO \
        --account 36328961555 --gl-bank 1060 --min-date 2025-01-01
    # add --commit to write to BigQuery + approval queue
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--client", required=True)
    ap.add_argument("--period", required=True)
    ap.add_argument("--bank", default=None)
    ap.add_argument("--account", default="xxxx")
    ap.add_argument("--gl-bank", default="1060")
    ap.add_argument("--min-date", default=None)
    ap.add_argument("--threshold", type=float, default=0.80)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--no-queue", action="store_true",
                    help="skip approval_queue submit (e.g. re-run after a schema fix)")
    args = ap.parse_args()

    from sage50.bank_parser import parse_csv
    from models.banking import BankCode
    from sage50.categorization_rules import get_ruleset

    bank_code = BankCode(args.bank.upper()) if args.bank else None
    txns = parse_csv(args.csv, account_no=args.account, bank_code=bank_code)
    print(f"Parsed {len(txns)} transactions from {Path(args.csv).name}")

    min_date = date.fromisoformat(args.min_date) if args.min_date else None
    if min_date:
        before = len(txns)
        txns = [t for t in txns if t.txn_date >= min_date]
        print(f"min_date {min_date}: dropped {before - len(txns)} pre-fiscal rows, "
              f"{len(txns)} remain")

    rs = get_ruleset(args.client)
    if rs is None:
        print(f"No ruleset for client {args.client!r}; would use DEFAULT_RULES.")
        return 1

    auto = review = 0
    deposits = withdrawals = Decimal("0")
    print(f"\n{'date':<11} {'amount':>11}  {'gl':>5} {'conf':>4}  description")
    print("-" * 78)
    for t in sorted(txns, key=lambda x: x.txn_date):
        gl, name, conf_pct = rs.categorize(t.description, t.amount)
        conf = float(conf_pct) / 100.0
        flag = "AUTO" if conf >= args.threshold else "REV "
        if conf >= args.threshold:
            auto += 1
        else:
            review += 1
        if t.amount > 0:
            deposits += t.amount
        else:
            withdrawals += abs(t.amount)
        print(f"{t.txn_date.isoformat():<11} {str(t.amount):>11}  {gl:>5} "
              f"{conf:>4.2f} {flag} {name[:22]:<22} | {t.description[:40]}")

    print("-" * 78)
    print(f"TOTAL {len(txns)}  | auto={auto}  review={review}  "
          f"({auto*100//max(len(txns),1)}% auto)")
    print(f"deposits={deposits}  withdrawals={withdrawals}  net={deposits-withdrawals}")

    if not args.commit:
        print("\n[preview] nothing written. Re-run with --commit to book to BigQuery.")
        return 0

    print("\n[commit] running BookkeepingAgent (BQ + approval queue)...")
    from agents.bookkeeping import BookkeepingAgent
    from agents.base import TaskRequest, TaskType
    res = BookkeepingAgent().run(TaskRequest(
        task_type=TaskType.BOOKKEEPING_RUN,
        payload={
            "csv_path": args.csv,
            "client_id": args.client,
            "account_no": args.account,
            "gl_bank_account": args.gl_bank,
            "period": args.period,
            "bank_code": args.bank,
            "min_date": args.min_date,
            "threshold": args.threshold,
            "notify_chat": False,
            "queue_reviews": not args.no_queue,
        },
    ))
    print(f"status={res.status}")
    import json
    print(json.dumps(res.output, indent=2, default=str))
    return 0 if str(res.status).endswith("SUCCESS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
