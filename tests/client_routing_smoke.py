"""
tests/client_routing_smoke.py
Offline smoke test for multi-client routing (Session 14).

OFFLINE: no Gmail / GCP calls. Exercises the routing key extractor and the
CSV-backed client registry against a temporary CSV and embedded TD + BMO fixtures.
If the cached real OCR (data/test-client/bank_statment_january_2026-ocr.txt)
is present, it is also fed through extract_account_no for a real-data check.

Checks:
   1   extract_account_no pulls the full normalized account from TD fixture text
   2   extract_account_no picks the MOST FREQUENT match (OCR-typo robust)
   3   extract_account_no returns None when no account pattern is present
   4   load_registry keys the CSV by normalized full account digits
   5   ClientConfig.account_masked yields the xxxx<last4> form
   6   resolve() routes fixture text -> Concetta config
   7   resolve() returns None for noise text (quarantine path)
   8   mismatch: a different account in text does not resolve to Concetta
   9   load_registry raises FileNotFoundError for a missing CSV
  10   (optional) cached real Jan OCR -> 18905315443
  11   bank-agnostic: a BMO-format statement routes to the BMO client (no TD regex)
  12   find_account_in_text returns the matched registry key
  13   amounts/dates that look numeric do not false-match an account
  14   ambiguous: a statement naming TWO clients resolves to None (quarantine)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sage50.bank_statement_ocr_parser import extract_account_no
from core.client_registry import (
    ClientConfig,
    find_account_in_text,
    load_registry,
    normalize_account,
    resolve,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CONCETTA_ACCT = "1890-5315443"
CONCETTA_DIGITS = "18905315443"

# TD-style text: the real account repeated, plus two OCR-typo variants that
# must NOT win the Counter vote.
TD_TEXT = (
    "TD CANADA TRUST\n"
    "ACCOUNT STATEMENT\n"
    f"Account No. {CONCETTA_ACCT}\n"
    f"Account No. {CONCETTA_ACCT}\n"
    f"Account No. {CONCETTA_ACCT}\n"
    "CHEQUE IMG 1840-5315443\n"   # typo variant (single occurrence)
    "CHEQUE IMG 1890-5315445\n"   # typo variant (single occurrence)
    "BALANCE FORWARD  1,000.00\n"
)

# Different valid-format account belonging to nobody in the registry.
OTHER_TEXT = "Account No. 2222-7654321\nBALANCE FORWARD 50.00\n"

NOISE_TEXT = ("the quick brown fox jumps over the lazy dog " * 30) + "\n"

# Second client on a DIFFERENT bank, printed in BMO's account format. The TD
# regex (\d{4}-\d{7}) cannot match this — only the registry-driven search can.
THEO_ACCT = "3632 8961-555"
THEO_DIGITS = "36328961555"
BMO_TEXT = (
    "BMO Bank of Montreal\n"
    "Primary Chequing Account statement\n"
    f"Account number  {THEO_ACCT}\n"
    "Opening balance                 2,500.00\n"
    "01/15/2026  PAYROLL DEPOSIT      3,200.00\n"   # numeric noise, must not match
    f"Account number  {THEO_ACCT}\n"
    "Closing balance                 5,700.00\n"
)

CACHED_OCR = (
    Path(__file__).resolve().parents[1]
    / "data" / "test-client" / "bank_statment_january_2026-ocr.txt"
)


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

_passed = 0
_failed = 0


def check(label: str, cond: bool) -> None:
    global _passed, _failed
    mark = "PASS" if cond else "FAIL"
    if cond:
        _passed += 1
    else:
        _failed += 1
    print(f"  [{mark}] {label}")


def _seed_csv(path: Path) -> None:
    path.write_text(
        "account_no,r_folder,client_id,gl_bank_account,bank,sender_email\n"
        f"{CONCETTA_ACCT},Concetta Enterprises Inc,concetta,1060,TD,"
        "veromendez87@hotmail.com\n"
        f"{THEO_ACCT},Canadian Federation of theotherapy,theotherapy,1060,BMO,"
        "veromendez87@hotmail.com\n",
        encoding="utf-8",
    )


def main() -> int:
    # 1 — extract full normalized account from fixture
    check(
        "extract_account_no -> 18905315443",
        extract_account_no(TD_TEXT) == CONCETTA_DIGITS,
    )

    # 2 — most-frequent wins over typo variants
    check(
        "extract_account_no picks most frequent (typo-robust)",
        extract_account_no(TD_TEXT) == CONCETTA_DIGITS,
    )

    # 3 — None when no pattern present
    check("extract_account_no(noise) -> None", extract_account_no(NOISE_TEXT) is None)

    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "client_accounts.csv"
        _seed_csv(csv_path)
        registry = load_registry(path=csv_path)

        # 4 — keyed by normalized full digits
        check("registry keyed by normalized digits", CONCETTA_DIGITS in registry)

        cfg = registry[CONCETTA_DIGITS]
        # 5 — masked form
        check(
            "account_masked -> xxxx5443",
            isinstance(cfg, ClientConfig) and cfg.account_masked == "xxxx5443",
        )

        # 6 — resolve routes fixture -> Concetta
        resolved = resolve(TD_TEXT, registry)
        check(
            "resolve(fixture) -> Concetta",
            resolved is not None and resolved.client_id == "concetta"
            and resolved.r_folder == "Concetta Enterprises Inc",
        )

        # 7 — noise resolves to None (quarantine)
        check("resolve(noise) -> None", resolve(NOISE_TEXT, registry) is None)

        # 8 — unknown valid account resolves to None (mismatch / quarantine)
        check("resolve(other account) -> None", resolve(OTHER_TEXT, registry) is None)

        # 11 — bank-agnostic: a BMO-format statement routes to the BMO client.
        # The TD regex can't see this account; only the registry search can.
        check(
            "extract_account_no(BMO) -> None (TD regex blind to BMO format)",
            extract_account_no(BMO_TEXT) is None,
        )
        theo = resolve(BMO_TEXT, registry)
        check(
            "resolve(BMO statement) -> theotherapy",
            theo is not None and theo.client_id == "theotherapy"
            and theo.r_folder == "Canadian Federation of theotherapy",
        )

        # also: TD statement still routes to Concetta with both clients registered
        td = resolve(TD_TEXT, registry)
        check(
            "resolve(TD statement) -> concetta (no cross-talk)",
            td is not None and td.client_id == "concetta",
        )

        # 12 — find_account_in_text returns the matched normalized key
        check(
            "find_account_in_text(BMO) -> 36328961555",
            find_account_in_text(BMO_TEXT, registry) == THEO_DIGITS,
        )

        # 13 — numeric noise (amounts/dates) must not false-match an account
        check(
            "amounts/dates do not false-match",
            find_account_in_text(
                "Opening 2,500.00\n01/15/2026 PAYROLL 3,200.00\nClosing 5,700.00\n",
                registry,
            ) is None,
        )

        # 14 — a statement naming TWO registered clients is ambiguous -> quarantine
        ambiguous = TD_TEXT + "\n" + f"Account number  {THEO_ACCT}\n"
        check(
            "resolve(two clients) -> None (quarantine)",
            resolve(ambiguous, registry) is None,
        )

    # 9 — missing CSV raises FileNotFoundError
    missing = Path(tempfile.gettempdir()) / "vtx_no_such_registry_xyz.csv"
    if missing.exists():
        missing.unlink()
    raised = False
    try:
        load_registry(path=missing)
    except FileNotFoundError:
        raised = True
    check("load_registry(missing) raises FileNotFoundError", raised)

    # 10 — cached real OCR (optional)
    if CACHED_OCR.exists():
        text = CACHED_OCR.read_text(encoding="utf-8", errors="replace")
        got = extract_account_no(text)
        check(
            f"cached Jan OCR -> 18905315443 (got {got})",
            normalize_account(got or "") == CONCETTA_DIGITS,
        )
    else:
        print("  [SKIP] cached Jan OCR not present")

    print(f"\n{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
