"""
scripts/export_demo_json.py — bake the AcumenAI showcase demo artifact.

Runs the offline bookkeeping pipeline on the fictional Northview statement and writes
the captured five-beat payload to demo/demo_run.json. That JSON is the single artifact
the public showcase page (on orchelix.com) animates — no backend, no auth, no live data,
so the demo can never fail mid-pitch.

Deterministic + offline: reuses dashboard.demo.build_demo_payload() (MockBQClient; no GCP,
no network). Re-run any time the pipeline/categorization changes to refresh the artifact.

    python scripts/export_demo_json.py                 # write demo/demo_run.json
    python scripts/export_demo_json.py --out path.json # custom output path
    python scripts/export_demo_json.py --no-approve     # omit the approval beat
    python scripts/export_demo_json.py --print          # also echo the JSON to stdout
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from dashboard.demo import build_demo_payload  # noqa: E402

_DEFAULT_OUT = _ROOT / "demo" / "demo_run.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="Bake the AcumenAI showcase demo JSON.")
    ap.add_argument("--out", type=Path, default=_DEFAULT_OUT,
                    help=f"output path (default: {_DEFAULT_OUT})")
    ap.add_argument("--no-approve", action="store_true",
                    help="omit the human-approval beat")
    ap.add_argument("--print", dest="echo", action="store_true",
                    help="also print the JSON to stdout")
    args = ap.parse_args()

    payload = build_demo_payload(approve=not args.no_approve)
    text = json.dumps(payload, indent=2, ensure_ascii=False)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text + "\n", encoding="utf-8")

    beats = payload.get("beats", {})
    verify = beats.get("verify", {})
    cat = beats.get("categorize", {})
    print(f"  Wrote {args.out.relative_to(_ROOT)}")
    print(f"  Beats: {', '.join(beats.keys())}")
    print(f"  Balance chain: {verify.get('reconciled')}/{verify.get('total')} reconciled")
    print(f"  Categorized:   {cat.get('auto_categorized')}/{cat.get('total')} "
          f"({cat.get('auto_pct')}% hands-off), {cat.get('queued')} queued")
    print(f"  Pipeline ran in {payload.get('recap', {}).get('duration_ms')} ms")

    if args.echo:
        print(text)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
