---
description: "Create a git commit following vtx-os discipline: one logical change, imperative mood, never commit data/credentials, update PROJECT_STATUS.md if needed."
---

Create a git commit for the current staged or working-tree changes, following the project's git discipline.

Arguments: $ARGUMENTS (optional commit message hint)

## Step 1 — assess what's changed

Run `git status` and `git diff` (or `git diff --staged` if already staged). Identify:
- Which files changed and why they belong together
- Whether this is one logical change or multiple (if multiple, ask the user which to commit first)
- Whether any sensitive files are present: `data/test-client/`, `config/project.env`,
  `config/*.json`, `.env`, `*.log` — never stage these, warn the user if present

## Step 2 — check PROJECT_STATUS.md

If the change introduces or completes a feature, agent, script, or fix that is tracked in
`PROJECT_STATUS.md` (or should be), update it in the same commit. One commit = one logical
unit including its status record. Do not commit a feature and its status update separately.

## Step 3 — draft the commit message

Rules from CLAUDE.md:
- **Imperative mood**: "Add", "Fix", "Remove", "Refactor" — not "Added", "Fixed", "Removes"
- **Explain WHY, not what**: the diff already shows what changed; the message explains the reason
- **One sentence** is usually enough; a short body paragraph is fine for non-obvious context
- Keep the subject line under 72 characters

Bad: `"Updated bank_parser.py"`
Good: `"Apply abs() to withdrawal column in all 7 bank parsers to prevent sign flip"`

If $ARGUMENTS contains a message hint, use it as the basis; refine for tone and clarity.

## Step 4 — confirm before committing

Show the user:
1. The list of files to be staged
2. The draft commit message

Ask: "Stage these files and commit with this message?"

## Step 5 — commit

```bash
git add <specific files>
git commit -m "<message>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

Always add specific files by name — never `git add -A` or `git add .` (risks committing
data/ or credentials). After committing, run `git status` to confirm the working tree is clean.

## Notes
- Never use `--no-verify` to skip the pre-commit hook unless the user explicitly asks.
- Never amend a commit that has already been pushed.
- The guard-prod-writes hook logs commits with `--commit` in the message — that is expected.
