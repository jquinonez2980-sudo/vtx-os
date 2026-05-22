"""
agents/journal_entry.py
JournalEntryAgent — builds balanced double-entry journal entries from categorized
bank transactions and posts them into Sage 50 via the SDK bridge.

Handles TaskType.POST_JOURNAL_ENTRIES.

Required payload keys:
    bank_csv_path    (str)  — local path to bank statement CSV
    period           (str)  — "YYYY-MM"
    gl_bank_account  (str)  — GL code for the bank account, e.g. "1060"
    client_id        (str)  — activates client-specific categorization ruleset

Optional payload keys:
    account_no        (str)  — masked account number (default "xxxx")
    threshold         (float)— confidence threshold for auto-approve (default 0.80)
    suspense_account  (str)  — GL code for needs_review transactions (default "5900")
    sai_file          (str)  — Sage 50 .SAI file path (falls back to Secret Manager)
    sage50_user       (str)  — Sage 50 user (falls back to env/Secret Manager)
    sage50_password   (str)  — Sage 50 password (falls back to env/Secret Manager)
    dry_run           (bool) — build entries but skip the Sage 50 write (default False)

Returns TaskResult.output:
    posted              int  — entries successfully posted to Sage 50
    total               int  — entries attempted
    errors              int  — entries that failed
    posted_to_suspense  int  — entries routed to suspense account (needs_review)
    period              str
    results             list — per-entry {date, comment, posted, journal_no|error}
"""

from __future__ import annotations

import calendar
import sys
import uuid
from datetime import date
from decimal import Decimal

from agents.base import AgentBase, TaskRequest, TaskResult, TaskType
from models.banking import CategorizedTransaction, JournalEntryDraft, JournalEntryLine
from models.base import EventStatus


class JournalEntryAgent(AgentBase):
    agent_id = "journal-entry-agent"

    def handle(self, request: TaskRequest) -> TaskResult:
        from sage50.bank_parser import parse_csv
        from sage50.categorizer import DEFAULT_RULES, categorize_batch

        payload           = request.payload
        csv_path          = payload["bank_csv_path"]
        period            = payload.get("period", "")
        bank_account      = payload.get("gl_bank_account", "1060")
        client_id         = payload.get("client_id", "").lower()
        account_no        = payload.get("account_no", "xxxx")
        threshold         = float(payload.get("threshold", 0.80))
        suspense_account  = payload.get("suspense_account", "5900")
        dry_run           = bool(payload.get("dry_run", False))

        # Client-specific GL code → Sage 50 lId mapping (applied only at bridge post time)
        account_map: dict | None = None
        if "concetta" in client_id:
            from sage50.categorization_rules import CONCETTA_ACCOUNT_MAP
            account_map = CONCETTA_ACCOUNT_MAP

        # Re-parse and categorize (mirrors BookkeepingAgent — fast, deterministic)
        raw_txns = parse_csv(csv_path, account_no=account_no)
        if "concetta" in client_id:
            from agents.bookkeeping import _categorize_concetta
            categorized = _categorize_concetta(raw_txns, threshold=threshold)
        else:
            categorized = categorize_batch(raw_txns, rules=DEFAULT_RULES, threshold=threshold)

        # Route needs_review transactions to the suspense account instead of skipping.
        # They are still posted to Sage 50 and can be reclassified from suspense later.
        to_post = []
        suspense_count = 0
        for t in categorized:
            if t.needs_review:
                to_post.append(t.model_copy(update={"gl_account_no": suspense_account}))
                suspense_count += 1
            else:
                to_post.append(t)

        drafts = _build_drafts(to_post, bank_account)

        if dry_run or not drafts:
            return TaskResult(
                task_id=request.task_id,
                task_type=request.task_type,
                agent_id=self.agent_id,
                status=EventStatus.SUCCESS,
                output={
                    "posted":             0,
                    "total":              len(drafts),
                    "errors":             0,
                    "posted_to_suspense": suspense_count,
                    "period":             period,
                    "dry_run":            True,
                    "drafts":             [_draft_to_dict(d) for d in drafts],
                },
            )

        # Entry-level idempotency — skip individual drafts already posted to Sage 50.
        # Key: (entry_date_iso, description_39chars, abs_amount_2dp)
        # Each BNK journal entry writes two GL lines (debit + credit), both with the
        # same abs amount and description, so max(debit, credit) produces the same key
        # for either line — the set deduplicates them automatically.
        # If the bridge is unavailable the check is skipped with a warning; entries are
        # posted and the purge_duplicates.py script can clean up any duplicates afterward.
        existing_keys: set[tuple[str, str, str]] = set()
        skipped_count = 0
        if period:
            try:
                from sage50.bridge_reader import fetch_gl_transactions
                year, month  = int(period[:4]), int(period[5:7])
                period_start = date(year, month, 1)
                period_end   = date(year, month, calendar.monthrange(year, month)[1])
                existing_rows = fetch_gl_transactions(
                    start_date=period_start,
                    end_date=period_end,
                    sai_file=payload.get("sai_file"),
                    user=payload.get("sage50_user"),
                    password=payload.get("sage50_password"),
                )
                for r in existing_rows:
                    if (r.source.upper() == "BNK"
                            and r.transaction_date is not None
                            and period_start <= r.transaction_date <= period_end):
                        abs_amt = max(r.debit, r.credit)
                        existing_keys.add((
                            r.transaction_date.isoformat(),
                            r.description[:39],
                            f"{abs_amt:.2f}",
                        ))
                if existing_keys:
                    print(
                        f"[journal-entry-agent] {len(existing_keys)} existing BNK key(s) "
                        f"found for {period} — will skip matching drafts",
                        file=sys.stderr,
                    )
            except Exception as exc:
                print(
                    f"[journal-entry-agent] WARNING: idempotency check skipped "
                    f"(bridge unavailable: {exc})",
                    file=sys.stderr,
                )

        filtered_drafts: list = []
        for draft in drafts:
            key = (
                draft.entry_date.isoformat(),
                draft.description[:39],
                f"{abs(draft.debit_line.debit):.2f}",
            )
            if key in existing_keys:
                print(
                    f"[journal-entry-agent] SKIPPED duplicate: "
                    f"{draft.entry_date} {draft.description[:25]!r} "
                    f"{abs(draft.debit_line.debit):.2f}",
                    file=sys.stderr,
                )
                skipped_count += 1
            else:
                filtered_drafts.append(draft)

        if skipped_count:
            print(
                f"[journal-entry-agent] Skipped {skipped_count} duplicate(s), "
                f"posting {len(filtered_drafts)} new entries",
                file=sys.stderr,
            )

        if not filtered_drafts:
            return TaskResult(
                task_id=request.task_id,
                task_type=request.task_type,
                agent_id=self.agent_id,
                status=EventStatus.SUCCESS,
                output={
                    "posted":             0,
                    "total":              len(drafts),
                    "errors":             0,
                    "posted_to_suspense": suspense_count,
                    "skipped_duplicates": skipped_count,
                    "period":             period,
                },
            )

        from sage50.bridge_reader import post_journal_entries
        bridge = post_journal_entries(
            [_draft_to_bridge(d, account_map) for d in filtered_drafts],
            sai_file=payload.get("sai_file"),
            user=payload.get("sage50_user"),
            password=payload.get("sage50_password"),
        )

        posted = bridge.get("posted", 0)
        total  = bridge.get("total",  len(filtered_drafts))
        errors = bridge.get("errors", 0)

        return TaskResult(
            task_id=request.task_id,
            task_type=request.task_type,
            agent_id=self.agent_id,
            status=EventStatus.SUCCESS if errors == 0 else EventStatus.PARTIAL,
            output={
                "posted":             posted,
                "total":              total,
                "errors":             errors,
                "posted_to_suspense": suspense_count,
                "skipped_duplicates": skipped_count,
                "period":             period,
                "results":            bridge.get("results", []),
            },
        )


# ---------------------------------------------------------------------------
# Journal entry construction
# ---------------------------------------------------------------------------

def _build_drafts(
    txns: list[CategorizedTransaction],
    bank_account: str,
) -> list[JournalEntryDraft]:
    drafts = []
    for txn in txns:
        abs_amt = abs(txn.amount)
        desc    = txn.description[:39]          # Sage 50 comment field max

        if txn.amount > 0:                      # Deposit  → Dr Bank / Cr Revenue
            debit_acct  = bank_account
            credit_acct = txn.gl_account_no or "9999"
        else:                                   # Withdrawal → Dr Expense / Cr Bank
            debit_acct  = txn.gl_account_no or "9999"
            credit_acct = bank_account

        drafts.append(JournalEntryDraft(
            draft_id      = str(uuid.uuid4()),
            entry_date    = txn.txn_date,
            reference     = txn.reference or "",
            description   = desc,
            source_txn_id = txn.txn_id,
            debit_line    = JournalEntryLine(
                account_no   = debit_acct,
                account_name = "",
                description  = desc,
                debit        = abs_amt,
                credit       = Decimal("0"),
            ),
            credit_line   = JournalEntryLine(
                account_no   = credit_acct,
                account_name = "",
                description  = desc,
                debit        = Decimal("0"),
                credit       = abs_amt,
            ),
        ))
    return drafts


def _draft_to_bridge(draft: JournalEntryDraft, account_map: dict | None = None) -> dict:
    """Convert a JournalEntryDraft to the wire format the C# bridge expects.

    account_map translates internal 4-digit GL codes to client-specific Sage 50 lId values.
    """
    def _sid(code: str) -> str:
        return account_map[code] if account_map and code in account_map else code

    return {
        "date":    draft.entry_date.isoformat(),  # "YYYY-MM-DD" → bridge converts to MM/DD/YYYY
        "source":  "BNK",
        "comment": draft.description,
        "lines": [
            {
                "account_id": _sid(draft.debit_line.account_no),
                "debit":      float(draft.debit_line.debit),
                "credit":     0.0,
                "comment":    draft.description,
            },
            {
                "account_id": _sid(draft.credit_line.account_no),
                "debit":      0.0,
                "credit":     float(draft.credit_line.credit),
                "comment":    draft.description,
            },
        ],
    }


def _draft_to_dict(draft: JournalEntryDraft) -> dict:
    return {
        "date":        draft.entry_date.isoformat(),
        "description": draft.description,
        "debit":  {"account": draft.debit_line.account_no,  "amount": str(draft.debit_line.debit)},
        "credit": {"account": draft.credit_line.account_no, "amount": str(draft.credit_line.credit)},
        "balanced": draft.is_balanced,
    }
