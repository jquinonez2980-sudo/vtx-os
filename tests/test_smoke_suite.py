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

import inspect
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


def test_mock_bq_client_query_contract() -> None:
    """MockBQClient.query must only name kwargs that bigquery.Client.query also accepts.

    This is the M0.1 contract-fidelity guard from the 2026-06-12 audit (C1/T1):
    the original mock accepted job_configuration= while the real client only has
    job_config=, so every approve/reject/escalate DML silently no-oped in
    production while all 19 offline suites stayed green.

    If this test fails after a library upgrade, update the mock to match the new
    real signature — never update the real call to match the mock.
    """
    from google.cloud import bigquery
    from tests.p1_7_e2e import MockBQClient

    real_params = set(inspect.signature(bigquery.Client.query).parameters.keys())

    # The mock names its first positional arg 'sql'; the real client names it
    # 'query'. They're positionally equivalent — exclude from the kwarg check.
    POSITIONAL_ALIASES = {"self", "sql"}

    mock_kwargs = {
        name
        for name, p in inspect.signature(MockBQClient.query).parameters.items()
        if name not in POSITIONAL_ALIASES
        and p.kind not in (
            inspect.Parameter.VAR_POSITIONAL,  # *args
            inspect.Parameter.VAR_KEYWORD,     # **_ / **kwargs
        )
    }

    extra = mock_kwargs - real_params
    assert not extra, (
        f"MockBQClient.query names {sorted(extra)} which bigquery.Client.query "
        f"(v{bigquery.__version__}) does not have. "
        f"The mock is encoding a contract the installed client doesn't honour — "
        f"the suite would pass while production breaks. "
        f"Real params: {sorted(real_params - {'self'})}"
    )


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
