"""
agents/reconcile_gl.py
ReconcileGLAgent — match bank statement transactions against Sage 50 GL entries.

Handles TaskType.RECONCILE_GL.

Required payload keys:
    gl_csv_path      (str)  — path to Sage 50 GL export CSV
    account_no       (str)  — masked bank account identifier, e.g. "xxxx5443"
    period           (str)  — "YYYY-MM" for the statement month

Optional payload keys:
    gl_bank_account  (str)   — GL account number for the bank account (default "1060")
    amount_tolerance (float) — max $ diff for a match (default 1.00)
    date_tolerance_days (int)— max days apart for a match (default 2)
    bank_csv_path    (str)   — if provided, re-parse bank CSV instead of querying BQ
                               (used for offline/testing)

Returns TaskResult.output as a ReconciliationSummary dict with:
    period, account_no, gl_bank_account,
    bank_txn_count, gl_entry_count,
    matched_count, unmatched_bank_count, unmatched_gl_count,
    total_bank_deposits, total_bank_withdrawals,
    total_gl_debits, total_gl_credits,
    bank_net, gl_net, net_difference, is_reconciled,
    bq_results_table
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

from agents.base import AgentBase, TaskRequest, TaskResult, TaskType
from core.bq_loader import ensure_table, load_rows
from models.banking import BankCode, BankTransaction
from models.base import EventStatus
from models.reconciliation import (
    GLEntry,
    MatchStatus,
    ReconciliationItem,
    ReconciliationSummary,
)
from sage50.gl_parser import parse_gl_csv

DATASET = "vtx_accounting"
RECON_TABLE = "gl_reconciliation"

_RECON_CFG = {
    "partition_field": "reconciliation_date",
    "cluster_fields":  ["match_status", "account_no"],
}


class ReconcileGLAgent(AgentBase):
    agent_id = "reconcile-gl-agent"

    def handle(self, request: TaskRequest) -> TaskResult:
        payload = request.payload

        gl_csv_path      = payload.get("gl_csv_path")
        if not gl_csv_path:
            return TaskResult(
                task_id=request.task_id,
                task_type=TaskType.RECONCILE_GL,
                agent_id=self.agent_id,
                status=EventStatus.FAILURE,
                error=(
                    "gl_csv_path is required — either pass an existing GL export CSV "
                    "or run with post_to_sage50=True to auto-fetch from Sage 50 after posting"
                ),
            )
        account_no       = payload["account_no"]
        period           = payload["period"]
        gl_bank_account  = payload.get("gl_bank_account", "1060")
        amount_tolerance = Decimal(str(payload.get("amount_tolerance", "1.00")))
        date_tolerance   = int(payload.get("date_tolerance_days", 2))

        # --- 1. Load GL entries (filtered to bank account) ---
        gl_entries = parse_gl_csv(gl_csv_path, gl_bank_account)
        if not gl_entries:
            return TaskResult(
                task_id=request.task_id,
                task_type=TaskType.RECONCILE_GL,
                agent_id=self.agent_id,
                status=EventStatus.FAILURE,
                error=f"No GL entries found for account {gl_bank_account} in {gl_csv_path}",
            )

        # --- 2. Load bank transactions ---
        # When bank_csv_path is supplied it is the authoritative source — never
        # fall back to BQ, which may not have these transactions at all.
        if bank_csv_path := payload.get("bank_csv_path"):
            bank_txns = _load_bank_txns_from_csv(bank_csv_path, account_no, period)
        else:
            bank_txns = _load_bank_txns_from_bq(account_no, period, gl_bank_account)

        if not bank_txns:
            return TaskResult(
                task_id=request.task_id,
                task_type=TaskType.RECONCILE_GL,
                agent_id=self.agent_id,
                status=EventStatus.FAILURE,
                error=f"No bank transactions found for account {account_no} period {period}",
            )

        # --- 3. Reconcile ---
        items = _reconcile(
            bank_txns, gl_entries,
            period, account_no, gl_bank_account,
            amount_tolerance, date_tolerance,
        )

        # --- 4. Compute summary ---
        summary = _summarise(items, bank_txns, gl_entries, period, account_no, gl_bank_account)

        # --- 5. Write to BQ ---
        bq_table = ensure_table(DATASET, RECON_TABLE, ReconciliationItem, **_RECON_CFG)
        load_rows(DATASET, RECON_TABLE, items, session_id=request.session_id)
        summary.bq_results_table = bq_table

        return TaskResult(
            task_id=request.task_id,
            task_type=TaskType.RECONCILE_GL,
            agent_id=self.agent_id,
            status=EventStatus.SUCCESS,
            output=summary.model_dump(mode="json"),
        )


# ---------------------------------------------------------------------------
# Matching engine
# ---------------------------------------------------------------------------

def _ref_match(bank_desc: str, gl_source: str) -> bool:
    """True if a 4+ digit reference from the bank description appears in the GL source number."""
    bank_refs = re.findall(r"\d{4,}", bank_desc)
    if not bank_refs:
        return False
    gl_digits = re.sub(r"[^0-9]", "", gl_source)
    return any(ref in gl_digits for ref in bank_refs)


def _reconcile(
    bank_txns: list[BankTransaction],
    gl_entries: list[GLEntry],
    period: str,
    account_no: str,
    gl_bank_account: str,
    amount_tolerance: Decimal,
    date_tolerance_days: int,
) -> list[ReconciliationItem]:
    """Greedy best-first matching of bank transactions to GL entries.

    Each GL entry can be matched at most once.
    Unmatched bank transactions → UNMATCHED_BANK.
    Remaining GL entries → UNMATCHED_GL.
    """
    matched_gl_idxs: set[int] = set()
    items: list[ReconciliationItem] = []

    common = dict(period=period, account_no=account_no, gl_bank_account=gl_bank_account)

    for bank_txn in sorted(bank_txns, key=lambda t: t.txn_date):
        best_score:  float | None = None
        best_idx:    int   | None = None
        best_gl:     GLEntry | None = None

        for idx, gl in enumerate(gl_entries):
            if idx in matched_gl_idxs:
                continue

            # Direction filter — same sign means same flow direction
            if bank_txn.amount > 0 and gl.gl_net_amount <= 0:
                continue
            if bank_txn.amount < 0 and gl.gl_net_amount >= 0:
                continue

            # Amount gate
            amount_diff = abs(abs(bank_txn.amount) - abs(gl.gl_net_amount))
            if amount_diff > amount_tolerance:
                continue

            # Date gate
            date_diff = abs((bank_txn.txn_date - gl.entry_date).days)
            if date_diff > date_tolerance_days:
                continue

            # Score: closer amount wins, then closer date, then matching reference
            score = float(amount_tolerance - amount_diff) * 100.0
            score += float(date_tolerance_days - date_diff) * 10.0
            if _ref_match(bank_txn.description, gl.source_no):
                score += 50.0

            if best_score is None or score > best_score:
                best_score = score
                best_idx   = idx
                best_gl    = gl

        if best_gl is not None and best_idx is not None:
            matched_gl_idxs.add(best_idx)
            amount_diff   = abs(abs(bank_txn.amount) - abs(best_gl.gl_net_amount))
            date_diff_days = abs((bank_txn.txn_date - best_gl.entry_date).days)
            items.append(ReconciliationItem(
                **common,
                match_status=MatchStatus.MATCHED,
                reconciliation_date=bank_txn.txn_date,
                bank_txn_id=bank_txn.txn_id,
                bank_date=bank_txn.txn_date,
                bank_description=bank_txn.description,
                bank_amount=bank_txn.amount,
                gl_source_no=best_gl.source_no,
                gl_date=best_gl.entry_date,
                gl_description=best_gl.description,
                gl_amount=best_gl.gl_net_amount,
                amount_diff=amount_diff,
                date_diff_days=date_diff_days,
            ))
        else:
            items.append(ReconciliationItem(
                **common,
                match_status=MatchStatus.UNMATCHED_BANK,
                reconciliation_date=bank_txn.txn_date,
                bank_txn_id=bank_txn.txn_id,
                bank_date=bank_txn.txn_date,
                bank_description=bank_txn.description,
                bank_amount=bank_txn.amount,
            ))

    # Remaining unmatched GL entries
    for idx, gl in enumerate(gl_entries):
        if idx not in matched_gl_idxs:
            items.append(ReconciliationItem(
                **common,
                match_status=MatchStatus.UNMATCHED_GL,
                reconciliation_date=gl.entry_date,
                gl_source_no=gl.source_no,
                gl_date=gl.entry_date,
                gl_description=gl.description,
                gl_amount=gl.gl_net_amount,
            ))

    return items


# ---------------------------------------------------------------------------
# Summary calculation
# ---------------------------------------------------------------------------

def _summarise(
    items: list[ReconciliationItem],
    bank_txns: list[BankTransaction],
    gl_entries: list[GLEntry],
    period: str,
    account_no: str,
    gl_bank_account: str,
) -> ReconciliationSummary:
    matched        = sum(1 for i in items if i.match_status == MatchStatus.MATCHED)
    unmatched_bank = sum(1 for i in items if i.match_status == MatchStatus.UNMATCHED_BANK)
    unmatched_gl   = sum(1 for i in items if i.match_status == MatchStatus.UNMATCHED_GL)

    total_bank_deposits    = sum((t.amount for t in bank_txns if t.amount > 0), Decimal("0"))
    total_bank_withdrawals = sum((abs(t.amount) for t in bank_txns if t.amount < 0), Decimal("0"))
    bank_net = total_bank_deposits - total_bank_withdrawals

    total_gl_debits  = sum((g.debit  for g in gl_entries), Decimal("0"))
    total_gl_credits = sum((g.credit for g in gl_entries), Decimal("0"))
    gl_net = total_gl_debits - total_gl_credits

    net_difference = gl_net - bank_net
    is_reconciled  = (unmatched_bank == 0 and unmatched_gl == 0)

    return ReconciliationSummary(
        period=period,
        account_no=account_no,
        gl_bank_account=gl_bank_account,
        bank_txn_count=len(bank_txns),
        gl_entry_count=len(gl_entries),
        matched_count=matched,
        unmatched_bank_count=unmatched_bank,
        unmatched_gl_count=unmatched_gl,
        total_bank_deposits=total_bank_deposits,
        total_bank_withdrawals=total_bank_withdrawals,
        total_gl_debits=total_gl_debits,
        total_gl_credits=total_gl_credits,
        bank_net=bank_net,
        gl_net=gl_net,
        net_difference=net_difference,
        is_reconciled=is_reconciled,
        bq_results_table="",   # filled by caller after ensure_table
    )


# ---------------------------------------------------------------------------
# Bank transaction sources
# ---------------------------------------------------------------------------

def _load_bank_txns_from_csv(
    csv_path: str,
    account_no: str,
    period: str,
) -> list[BankTransaction]:
    """Parse a bank statement CSV and return transactions for the period.

    If the period filter drops everything (e.g. the PDF extractor produced
    slightly different date strings), return all parsed rows so the caller
    is never silently left empty when the file contains valid transactions.
    """
    from sage50.bank_parser import parse_csv
    txns = parse_csv(csv_path, account_no=account_no)
    if not txns:
        return []
    filtered = [t for t in txns if t.txn_date.strftime("%Y-%m") == period]
    return filtered if filtered else txns


def _load_bank_txns_from_bq(
    account_no: str,
    period: str,
    gl_bank_account: str = "1060",
) -> list[BankTransaction]:
    """Query vtx_accounting.bank_transactions_raw for the period.

    Two-tier matching:
      1. Exact account_no OR suffix match (e.g. 'xxxx5443' or '5443' both resolve).
      2. If still empty, fall back to bank_transactions_categorized filtered by
         gl_account_no (the Sage 50 bank account code, e.g. '1060'), joined back
         to raw for the BankTransaction fields.
    """
    from core.bq_loader import _bq
    from google.cloud.bigquery import QueryJobConfig, ScalarQueryParameter

    year, month = int(period[:4]), int(period[5:7])
    last_day = calendar.monthrange(year, month)[1]
    start = date(year, month, 1).isoformat()
    end   = date(year, month, last_day).isoformat()

    # Extract the numeric digits for a suffix LIKE match.
    # "xxxx5443" → "%5443";  "5443" → "%5443";  "xxxx" (no digits) → skip LIKE.
    digits = re.sub(r"[^0-9]", "", account_no)
    account_suffix = f"%{digits}" if digits else account_no

    def _rows_to_txns(job_result) -> list[BankTransaction]:
        txns: list[BankTransaction] = []
        for row in job_result:
            txns.append(BankTransaction(
                txn_id=row.txn_id,
                bank_code=BankCode(row.bank_code),
                account_no=row.account_no,
                txn_date=row.txn_date,
                description=row.description,
                raw_description=row.raw_description or "",
                amount=Decimal(str(row.amount)),
                balance=Decimal(str(row.balance)) if row.balance else None,
                reference=row.reference,
            ))
        return txns

    # --- Tier 1: raw table by account_no (exact + suffix) ---
    sql1 = """
        SELECT txn_id, bank_code, account_no, txn_date, description,
               raw_description, amount, balance, reference
        FROM `vtx-accounting-os-prod.vtx_accounting.bank_transactions_raw`
        WHERE (account_no = @account_no OR account_no LIKE @account_suffix)
          AND txn_date BETWEEN @start_date AND @end_date
        ORDER BY txn_date
    """
    cfg1 = QueryJobConfig(query_parameters=[
        ScalarQueryParameter("account_no",     "STRING", account_no),
        ScalarQueryParameter("account_suffix", "STRING", account_suffix),
        ScalarQueryParameter("start_date",     "DATE",   start),
        ScalarQueryParameter("end_date",       "DATE",   end),
    ])
    txns = _rows_to_txns(_bq().query(sql1, job_config=cfg1).result())
    if txns:
        return txns

    # --- Tier 2: categorized table by GL bank account code ---
    # Transactions may be stored under a different account_no label; the
    # categorized table identifies them by gl_account_no (e.g. "1060").
    sql2 = """
        SELECT r.txn_id, r.bank_code, r.account_no, r.txn_date, r.description,
               r.raw_description, r.amount, r.balance, r.reference
        FROM `vtx-accounting-os-prod.vtx_accounting.bank_transactions_raw` r
        JOIN `vtx-accounting-os-prod.vtx_accounting.bank_transactions_categorized` c
          ON r.txn_id = c.txn_id
        WHERE c.gl_account_no = @gl_bank_account
          AND r.txn_date BETWEEN @start_date AND @end_date
        ORDER BY r.txn_date
    """
    cfg2 = QueryJobConfig(query_parameters=[
        ScalarQueryParameter("gl_bank_account", "STRING", gl_bank_account),
        ScalarQueryParameter("start_date",      "DATE",   start),
        ScalarQueryParameter("end_date",        "DATE",   end),
    ])
    return _rows_to_txns(_bq().query(sql2, job_config=cfg2).result())
