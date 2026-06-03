"""
scripts/year_end.py
Generate the year-end Excel worksheet for a client.

Usage:
    python scripts/year_end.py --client concetta --period 2026-04
    python scripts/year_end.py --client concetta --period 2026-04 --tb-csv R:\\path\\tb.csv
    python scripts/year_end.py --client concetta --period 2026-04 --dry-run

Flow:
  1. Load client registry; resolve client by --client slug
  2. Warn if --period month does not match client's year_end_month
  3. Locate trial balance CSV (--tb-csv override or auto-find in drop folder)
  4. Parse TB; report account count
  5. Build output path: R:\\{r_folder}\\Year End\\{client_id}_yearend_{period}.xlsx
  6. Call populate_worksheet (skipped on --dry-run)
  7. Print summary
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from core.client_registry import load_registry
from core.year_end_worksheet import populate_worksheet
from sage50.trial_balance_parser import find_tb_csv, parse_trial_balance

TEMPLATE_PATH = Path(r"R:\Templates\TEMPLATE_YearEnd_Accounting_Professional_BLANK_v2.xlsx")

_MONTH_NAMES = {
    1: "January", 2: "February", 3: "March",  4: "April",
    5: "May",     6: "June",     7: "July",    8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}


def _year_end_label(period: str) -> str:
    """Convert '2026-04' to 'April 30, 2026'."""
    try:
        year, month = period.split("-")
        import calendar
        last_day = calendar.monthrange(int(year), int(month))[1]
        return f"{_MONTH_NAMES[int(month)]} {last_day}, {year}"
    except Exception:
        return period


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate year-end Excel worksheet")
    parser.add_argument("--client",  required=True, help="Client ID slug (e.g. concetta)")
    parser.add_argument("--period",  required=True, help="Fiscal year-end period YYYY-MM (e.g. 2026-04)")
    parser.add_argument("--tb-csv",  help="Override path to trial balance CSV")
    parser.add_argument("--dry-run", action="store_true", help="Parse and validate only; do not write Excel")
    args = parser.parse_args()

    # --- 1. Load registry ---
    registry = load_registry()
    cfg = next((c for c in registry.values() if c.client_id == args.client), None)
    if cfg is None:
        print(f"ERROR: client '{args.client}' not found in registry.", file=sys.stderr)
        sys.exit(1)

    print(f"Client : {cfg.r_folder} ({cfg.client_id})")
    print(f"Period : {args.period}")

    # --- 2. Year-end month validation ---
    try:
        period_month = int(args.period.split("-")[1])
    except (IndexError, ValueError):
        print(f"ERROR: --period must be YYYY-MM format, got: {args.period}", file=sys.stderr)
        sys.exit(1)

    if cfg.year_end_month and period_month != cfg.year_end_month:
        expected = _MONTH_NAMES.get(cfg.year_end_month, str(cfg.year_end_month))
        actual   = _MONTH_NAMES.get(period_month, str(period_month))
        print(
            f"WARNING: period month is {actual} but client year-end is {expected}. "
            "Continuing anyway — verify this is intentional."
        )

    # --- 3. Locate TB CSV ---
    if args.tb_csv:
        tb_csv = Path(args.tb_csv)
    else:
        drop_dir = Path(r"R:") / cfg.r_folder / "drop"
        tb_csv = find_tb_csv(drop_dir, args.period)

    print(f"TB CSV : {tb_csv}")

    # --- 4. Parse TB ---
    tb_lines = parse_trial_balance(tb_csv)
    total_debit  = sum(l.debit  for l in tb_lines)
    total_credit = sum(l.credit for l in tb_lines)
    print(f"Accounts : {len(tb_lines)} posting accounts")
    print(f"TB Debit : {total_debit:,.2f}   TB Credit : {total_credit:,.2f}")
    if abs(total_debit - total_credit) > 0:
        print(f"WARNING: TB is out of balance by {abs(total_debit - total_credit):,.2f}")

    if args.dry_run:
        print("\n[dry-run] Skipping Excel generation.")
        return

    # --- 5. Build output path ---
    output_dir  = Path(r"R:") / cfg.r_folder / "Year End"
    output_path = output_dir / f"{cfg.client_id}_yearend_{args.period}.xlsx"

    # --- 6. Populate worksheet ---
    year_end_label = _year_end_label(args.period)
    populate_worksheet(
        template_path=TEMPLATE_PATH,
        output_path=output_path,
        client_name=cfg.r_folder,
        year_end_date=year_end_label,
        tb_lines=tb_lines,
    )

    print(f"\nOutput : {output_path}")
    print("Done.")


if __name__ == "__main__":
    main()
