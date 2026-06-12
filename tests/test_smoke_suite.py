"""
tests/test_smoke_suite.py
Pytest entry point for the project's offline test suite.

The project's tests are standalone scripts (each prints "N/N checks" and exits 1
on failure), not pytest functions. This wrapper runs every offline smoke script
as a subprocess in one parametrized suite, excluding the live GCP tests. It sets
PYTHONUTF8 so the scripts never hit the Windows cp1252 charmap crash regardless
of the parent session's env.

    python -m pytest tests/test_smoke_suite.py        # run the whole offline suite
    python -m pytest tests/test_smoke_suite.py -k categorization   # a subset
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_TESTS = _ROOT / "tests"
_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}


# Real-client fixtures live in gitignored data/test-client/ and exist only on
# the bookkeeping machine. On CI those scripts are SKIPPED (not failed) — the
# data must never be committed. Keep this map in sync when adding data-driven
# smoke tests; the long-term fix is synthetic fixtures (audit task M3).
_DATA_DEPS: dict[str, list[str]] = {
    "p1_7_e2e.py":                      ["data/test-client/dec-2025-bank.csv"],
    "concetta_categorization_smoke.py": ["data/test-client/dec-2025-bank-extracted.csv"],
    "client_routing_smoke.py":          ["data/test-client/bank_statment_january_2026-ocr.txt"],
    "p2_2_a2a_smoke.py":                ["data/test-client/concetta-dec2025-gl.csv",
                                         "data/test-client/dec-2025-bank-extracted.csv"],
    "journal_entry_smoke.py":           ["data/test-client/dec-2025-bank-extracted.csv"],
    "p2_1_adk_smoke.py":                ["data/test-client/concetta-dec2025-gl.csv",
                                         "data/test-client/dec-2025-bank-extracted.csv"],
}


def _offline_scripts() -> list[Path]:
    scripts = sorted(_TESTS.glob("*_smoke.py"))
    e2e = _TESTS / "p1_7_e2e.py"
    if e2e.exists():
        scripts.append(e2e)
    # Exclude the live GCP tests (they need ADC and mutate prod).
    return [s for s in scripts if "live" not in s.name.lower()]


@pytest.mark.parametrize("script", _offline_scripts(), ids=lambda p: p.name)
def test_offline_smoke(script: Path) -> None:
    missing = [d for d in _DATA_DEPS.get(script.name, ())
               if not (_ROOT / d).exists()]
    if missing:
        pytest.skip(f"requires gitignored client data: {', '.join(missing)}")
    r = subprocess.run(
        [sys.executable, str(script)],
        cwd=_ROOT, env=_ENV, capture_output=True, text=True,
    )
    if r.returncode != 0:
        tail = (r.stdout + "\n" + r.stderr)[-3000:]
        pytest.fail(f"{script.name} exited {r.returncode}\n{tail}", pytrace=False)
