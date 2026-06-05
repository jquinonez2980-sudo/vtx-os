"""
dashboard/ — AcumenAI (by Orchelix) dashboard backend for vtx-os.

Phase A (shipped): an offline, deterministic capture of the bookkeeping pipeline
on fictional data, serialized to JSON for the public showcase page to animate
(see dashboard/demo.py + scripts/export_demo_json.py).

Phase B (later): a FastAPI JSON API (app.py / auth.py / queries.py) exposing live
BigQuery data behind JWT auth. See the plan in .claude/plans/.
"""
