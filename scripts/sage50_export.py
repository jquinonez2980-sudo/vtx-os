"""
scripts/sage50_export.py
Export Sage 50 data to drop-folder CSVs, replacing three manual UI exports:
  gl-{period}.csv          — General Journal (replaces Reports → General Journal → Export)
  tb-{period}.csv          — Trial Balance   (replaces Reports → Financials → Trial Balance → Export)
  tax-summary-{period}.csv — Tax Summary     (replaces Reports → Tax → Tax Summary → Export)

Sage 50 must be RUNNING with the company file open (SDK requires Connection Manager).

Usage:
    python scripts/sage50_export.py --client concetta --period 2026-04
    python scripts/sage50_export.py --client concetta --period 2026-04 --skip-gl
    python scripts/sage50_export.py --client concetta --period 2026-04 --skip-tb --skip-tax
    python scripts/sage50_export.py --client concetta --period 2026-04 --dry-run
    python scripts/sage50_export.py --client concetta --period 2026-04 --sage50-sai "R:\\path\\to\\file.sai"

Output directory: R:\\{client r_folder}\\drop\\
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from core.client_registry import load_registry


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Sage 50 data to drop-folder CSVs")
    parser.add_argument("--client",       required=True,  help="Client slug, e.g. concetta")
    parser.add_argument("--period",       required=True,  help="YYYY-MM period, e.g. 2026-04")
    parser.add_argument("--sage50-sai",   default=None,   help="Path to .SAI file (falls back to VTX_SAGE50_SAI env var or Secret Manager)")
    parser.add_argument("--sage50-user",  default=None)
    parser.add_argument("--sage50-password", default=None)
    parser.add_argument("--skip-gl",      action="store_true", help="Skip GL export")
    parser.add_argument("--skip-tb",      action="store_true", help="Skip Trial Balance export")
    parser.add_argument("--skip-tax",     action="store_true", help="Skip Tax Summary export")
    parser.add_argument("--tax-code",     default="H",    help="HST/GST tax code (default: H = Ontario 13%%)")
    parser.add_argument("--dry-run",      action="store_true", help="Parse and validate only; do not write files")
    args = parser.parse_args()

    period = args.period
    if len(period) != 7 or period[4] != "-":
        print(f"ERROR: --period must be YYYY-MM (got '{period}')", file=sys.stderr)
        sys.exit(1)

    # --- Resolve client ---
    registry = load_registry()
    cfg = next((c for c in registry.values() if c.client_id == args.client), None)
    if cfg is None:
        slugs = [c.client_id for c in registry.values()]
        print(f"ERROR: client '{args.client}' not found. Known: {slugs}", file=sys.stderr)
        sys.exit(1)

    drop_dir = Path(r"R:") / cfg.r_folder / "drop"
    gl_path  = drop_dir / f"gl-{period}.csv"
    tb_path  = drop_dir / f"tb-{period}.csv"
    tax_path = drop_dir / f"tax-summary-{period}.csv"

    sai      = args.sage50_sai
    s50_user = args.sage50_user
    s50_pass = args.sage50_password

    print(f"\n=== sage50_export  client={cfg.client_id}  period={period} ===")
    print(f"  drop dir : {drop_dir}")
    if args.dry_run:
        print("  [dry-run] files will NOT be written\n")
    else:
        print()

    from sage50.bridge_reader import (
        fetch_gl_csv,
        fetch_trial_balance_csv,
        fetch_tax_summary_csv,
    )

    errors = 0

    # ------------------------------------------------------------------
    # GL export
    # ------------------------------------------------------------------
    if not args.skip_gl:
        print("GL export...", end=" ", flush=True)
        if args.dry_run:
            print("(skipped - dry-run)")
        else:
            try:
                from sage50.categorization_rules import CONCETTA_ACCOUNT_MAP
                acct_map = CONCETTA_ACCOUNT_MAP if cfg.client_id == "concetta" else None
                n = fetch_gl_csv(
                    period, gl_path,
                    account_map=acct_map,
                    load_bq=False,
                    sai_file=sai, user=s50_user, password=s50_pass,
                )
                print(f"{n} rows  →  {gl_path.name}")
            except Exception as exc:
                print(f"FAILED: {exc}", file=sys.stderr)
                errors += 1

    # ------------------------------------------------------------------
    # Trial Balance export
    # ------------------------------------------------------------------
    if not args.skip_tb:
        print("Trial Balance export...", end=" ", flush=True)
        if args.dry_run:
            print("(skipped - dry-run)")
        else:
            try:
                n = fetch_trial_balance_csv(
                    period, tb_path,
                    sai_file=sai, user=s50_user, password=s50_pass,
                )
                print(f"{n} accounts  →  {tb_path.name}")
            except Exception as exc:
                print(f"FAILED: {exc}", file=sys.stderr)
                errors += 1

    # ------------------------------------------------------------------
    # Tax Summary export
    # ------------------------------------------------------------------
    if not args.skip_tax:
        print("Tax Summary export...", end=" ", flush=True)
        if args.dry_run:
            print("(skipped - dry-run)")
        else:
            try:
                summary = fetch_tax_summary_csv(
                    period, tax_path,
                    tax_code=args.tax_code,
                    sai_file=sai, user=s50_user, password=s50_pass,
                )
                print(
                    f"taxable_sales={summary['taxable_sales']}  "
                    f"HST_collected={summary['tax_collected']}  "
                    f"ITCs={summary['itc_claimed']}  "
                    f"net_tax={summary['net_tax']}"
                )
                print(f"  -> {tax_path.name}")
            except Exception as exc:
                print(f"FAILED: {exc}", file=sys.stderr)
                errors += 1

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print()
    if errors:
        print(f"Completed with {errors} error(s).")
        sys.exit(1)
    else:
        print("All exports completed successfully.")
        if not args.dry_run:
            print(f"\nDrop folder: {drop_dir}")
            if not args.skip_gl:
                print(f"  {gl_path.name}")
            if not args.skip_tb:
                print(f"  {tb_path.name}")
            if not args.skip_tax:
                print(f"  {tax_path.name}")
        print()


if __name__ == "__main__":
    main()
