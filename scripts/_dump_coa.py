"""Dump Concetta's chart of accounts — shows actual lId values for SetAccount mapping."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SAI  = r"R:\Concetta Enterprises Inc\2026.SAI"
USER = "sysadmin"
PASS = sys.argv[1] if len(sys.argv) > 1 else ""

from sage50.bridge_reader import _run_bridge

rows = _run_bridge(SAI, USER, PASS, "coa")
print(f"{'lId':<12} {'sName':<40} cFunc")
print("-" * 58)
for r in sorted(rows, key=lambda x: x.get("lId", 0)):
    print(f"{str(r.get('lId','')):<12} {str(r.get('sName','')):<40} {r.get('cFunc','')}")
