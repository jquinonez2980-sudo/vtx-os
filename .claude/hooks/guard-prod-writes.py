#!/usr/bin/env python
"""
.claude/hooks/guard-prod-writes.py
PreToolUse(Bash) guard for vtx-os: log + warn on production-mutation commands.

NON-BLOCKING by design — the auto-mode classifier remains the real gate. This
just keeps an audit trail of risky operations (Sage 50 posting, BigQuery DML,
secret writes, any --commit) and surfaces a one-line warning, so prod writes are
never invisible. Reads the hook JSON on stdin; matched commands are appended to
.claude/prod-writes.log (gitignored via *.log).
"""
import datetime
import json
import re
import sys
from pathlib import Path

PATTERNS = [
    (r"_post_je\.py", "Sage 50 journal posting"),
    (r"post_journal_entries", "Sage 50 journal posting"),
    (r"--commit\b", "a --commit write"),
    (r"\b(DELETE\s+FROM|UPDATE\s+\w)", "BigQuery/SQL DML"),
    (r"gcloud\s+secrets\s+versions\s+add", "writing a secret"),
]

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

cmd = (data.get("tool_input", {}) or {}).get("command", "") or ""
hits = sorted({label for pat, label in PATTERNS if re.search(pat, cmd, re.I)})
if not hits:
    sys.exit(0)

log = Path(__file__).resolve().parents[1] / "prod-writes.log"
try:
    with open(log, "a", encoding="utf-8") as fh:
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        fh.write(f"{ts}  {', '.join(hits)}  |  {cmd}\n")
except Exception:
    pass

print(json.dumps({
    "systemMessage": f"prod-write guard: {', '.join(hits)} — logged to .claude/prod-writes.log"
}))
sys.exit(0)
