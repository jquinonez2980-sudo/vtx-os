"""Preview all 20 journal entry drafts before live post."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.base import TaskRequest, TaskType
from agents.journal_entry import JournalEntryAgent

agent = JournalEntryAgent()
result = agent.handle(TaskRequest(
    task_type=TaskType.POST_JOURNAL_ENTRIES,
    payload={
        "bank_csv_path":   "data/test-client/dec-2025-bank-extracted.csv",
        "period":          "2025-12",
        "gl_bank_account": "1060",
        "client_id":       "concetta-enterprises",
        "account_no":      "xxxx5443",
        "dry_run":         True,
    },
))

o = result.output
print(f"Total drafts:       {o['total']}")
print(f"To suspense (5900): {o['posted_to_suspense']}")
print()
print(f"  {'Date':<12} {'Dr Acct':<8} {'Cr Acct':<8} {'Amount':>12}  Description")
print("  " + "-" * 70)
for d in o["drafts"]:
    print(f"  {d['date']:<12} {d['debit']['account']:<8} {d['credit']['account']:<8} "
          f"{d['debit']['amount']:>12}  {d['description']}")
