---
description: "Run the offline smoke suite (or one named test) via pytest. Excludes live GCP tests."
---

Run the project's offline test suite. The tests are script-style; `test_smoke_suite.py`
wraps every offline `*_smoke.py` (+ `p1_7_e2e.py`) into one parametrized pytest run.
Live GCP tests (`*_live*.py`, `concetta_live_pipeline.py`, `p2_7_live.py`) are excluded.

Arguments: $ARGUMENTS

## If $ARGUMENTS names a specific test

Run that single script directly (faster, full output):

```powershell
.venv\Scripts\python.exe tests\<name>.py
```

Accept either a bare name (`statement_extractor`) or full filename (`statement_extractor_smoke.py`);
resolve to the actual file under `tests\`.

## Otherwise — run the whole offline suite

```powershell
.venv\Scripts\python.exe -m pytest tests\test_smoke_suite.py
```

Add `-k <substring>` if $ARGUMENTS looks like a filter rather than a filename
(e.g. `/run-tests categorization` → `-k categorization`).

## Report

- On success: report the pass count (e.g. "all N offline smoke tests passed").
- On failure: show the failing script's output, name the failing test, and STOP.
  Do not proceed to commit or post anything until tests are green.

## Notes
- These tests mock BQ/Vertex/Gmail — no ADC or network required.
- The suite sets `PYTHONUTF8=1` itself, so it runs cleanly regardless of the parent shell.
- To run a LIVE test (writes prod BQ), invoke it directly and deliberately — not through this command.
