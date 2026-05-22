"""
BookkeepingAgent — parse bank statement CSV, categorize transactions, load to BigQuery.

Handles TaskType.BOOKKEEPING_RUN.

Required payload keys:
    csv_path     (str)  — local path OR gs://... URI to the bank statement CSV
    account_no   (str)  — masked account identifier, e.g. "xxxx1234"
    gl_bank_account (str) — GL code for this bank account, e.g. "1060"

Optional payload keys:
    bank_code    (str)  — BankCode value; auto-detected if omitted
    period       (str)  — "YYYY-MM" label for the statement, e.g. "2025-12"
    threshold    (float)— confidence threshold for auto-approve (default 0.80)
    rules_json   (str)  — path to a JSON file of custom CategorizationRule objects

Returns TaskResult.output as a BookkeepingSummary dict with:
    period, bank_code, account_no,
    total_transactions, auto_categorized, needs_review,
    total_deposits, total_withdrawals, net_movement,
    bq_raw_table, bq_categorized_table
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from agents.base import AgentBase, TaskRequest, TaskResult, TaskType
from core.bq_loader import ensure_table, load_rows
from models.banking import (
    BankCode,
    BankTransaction,
    BookkeepingSummary,
    CategorizedTransaction,
    CategorizationRule,
)
from models.base import EventStatus
from sage50.bank_parser import parse_csv
from sage50.categorizer import DEFAULT_RULES, categorize_batch

DATASET = "vtx_accounting"
RAW_TABLE         = "bank_transactions_raw"
CATEGORIZED_TABLE = "bank_transactions_categorized"

# Partition on txn_date, cluster by bank_code + account_no
_RAW_CFG = {
    "partition_field": "txn_date",
    "cluster_fields": ["bank_code", "account_no"],
}
_CAT_CFG = {
    "partition_field": "txn_date",
    "cluster_fields": ["bank_code", "gl_account_no"],
}


class BookkeepingAgent(AgentBase):
    agent_id = "bookkeeping-agent"

    def handle(self, request: TaskRequest) -> TaskResult:
        payload = request.payload

        csv_path  = payload["csv_path"]
        account_no = payload.get("account_no", "xxxx")
        gl_bank   = payload.get("gl_bank_account", "1060")
        period    = payload.get("period", "")
        threshold = float(payload.get("threshold", 0.80))

        bank_code: BankCode | None = None
        if bc := payload.get("bank_code"):
            bank_code = BankCode(bc.upper())

        client_id = payload.get("client_id", "").lower()

        # Load custom rules if provided
        rules = DEFAULT_RULES
        if rules_path := payload.get("rules_json"):
            rules = _load_rules(rules_path)

        # --- 1. Download from GCS if URI ---
        local_path = _resolve_path(csv_path, request.session_id)

        # --- 2. Parse ---
        raw_txns: list[BankTransaction] = parse_csv(
            local_path, account_no=account_no, bank_code=bank_code
        )
        if not raw_txns:
            return TaskResult(
                task_id=request.task_id,
                task_type=TaskType.BOOKKEEPING_RUN,
                agent_id=self.agent_id,
                status=EventStatus.FAILURE,
                error="No transactions parsed from CSV. Check file format and bank_code.",
            )

        # --- 3. Categorize ---
        if "concetta" in client_id:
            categorized: list[CategorizedTransaction] = _categorize_concetta(
                raw_txns, threshold=threshold
            )
        else:
            categorized = categorize_batch(raw_txns, rules=rules, threshold=threshold)

        # --- 4. Compute summary figures ---
        deposits    = sum((t.amount for t in raw_txns if t.amount > 0), Decimal("0"))
        withdrawals = sum((abs(t.amount) for t in raw_txns if t.amount < 0), Decimal("0"))
        auto_done   = sum(1 for t in categorized if not t.needs_review)
        needs_rev   = sum(1 for t in categorized if t.needs_review)

        # --- 5. Ensure BQ tables exist, then stream rows ---
        bq_raw  = ensure_table(DATASET, RAW_TABLE,         BankTransaction,        **_RAW_CFG)
        bq_cat  = ensure_table(DATASET, CATEGORIZED_TABLE, CategorizedTransaction, **_CAT_CFG)

        load_rows(DATASET, RAW_TABLE,         raw_txns,    session_id=request.session_id)
        load_rows(DATASET, CATEGORIZED_TABLE, categorized, session_id=request.session_id)

        resolved_period = period or _infer_period(raw_txns)

        # --- 6. Submit needs_review items to approval queue ---
        queue_items: list = []
        if needs_rev > 0 and payload.get("queue_reviews", True):
            from core.approval_queue import submit as queue_submit
            queue_items = queue_submit(
                categorized,
                session_id=request.session_id,
                period=resolved_period,
            )

        # --- 7. Google Chat notification ---
        chat_ok = False
        if queue_items and payload.get("notify_chat", True):
            from core.chat_notifier import notify_pending_reviews
            chat_ok = notify_pending_reviews(
                items=queue_items,
                period=resolved_period,
                bank_code=raw_txns[0].bank_code.value,
                account_no=account_no,
                summary={
                    "total_transactions": len(raw_txns),
                    "auto_categorized":   auto_done,
                    "needs_review":       needs_rev,
                    "net_movement":       str(deposits - withdrawals),
                },
            )

        summary = BookkeepingSummary(
            period=resolved_period,
            bank_code=raw_txns[0].bank_code.value,
            account_no=account_no,
            total_transactions=len(raw_txns),
            auto_categorized=auto_done,
            needs_review=needs_rev,
            total_deposits=deposits,
            total_withdrawals=withdrawals,
            net_movement=deposits - withdrawals,
            bq_raw_table=bq_raw,
            bq_categorized_table=bq_cat,
            queue_items_submitted=len(queue_items),
            chat_notified=chat_ok,
        )

        # mode="json" serialises Decimals → str, dates → ISO strings
        return TaskResult(
            task_id=request.task_id,
            task_type=TaskType.BOOKKEEPING_RUN,
            agent_id=self.agent_id,
            status=EventStatus.SUCCESS,
            output=summary.model_dump(mode="json"),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_path(csv_path: str, session_id: str) -> Path:
    """If csv_path is a GCS URI, download to a local temp file first."""
    if csv_path.startswith("gs://"):
        import tempfile
        from google.cloud import storage as gcs

        tmp = Path(tempfile.mktemp(suffix=".csv", prefix=f"vtx_{session_id}_"))
        bucket_name, blob_name = csv_path[5:].split("/", 1)
        gcs.Client().bucket(bucket_name).blob(blob_name).download_to_filename(str(tmp))
        return tmp
    return Path(csv_path)


def _infer_period(txns: list[BankTransaction]) -> str:
    """Infer YYYY-MM from the most common month in the transactions."""
    from collections import Counter
    counts = Counter(f"{t.txn_date.year}-{t.txn_date.month:02d}" for t in txns)
    return counts.most_common(1)[0][0] if counts else ""


def _load_rules(path: str) -> list[CategorizationRule]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return [CategorizationRule(**r) for r in data]


def _categorize_concetta(
    txns: list[BankTransaction],
    threshold: float,
) -> list[CategorizedTransaction]:
    """Bridge between ConcettaRuleset and CategorizedTransaction.

    ConcettaRuleset returns (gl_no: int, gl_name: str, confidence_pct: Decimal)
    where confidence is 0–100. Suspense (GL 5900, confidence 0) always needs_review.
    """
    from sage50.categorization_rules import ConcettaRuleset
    ruleset = ConcettaRuleset()
    result = []
    for txn in txns:
        gl_no_int, gl_name, confidence_pct = ruleset.categorize(txn.description, txn.amount)
        confidence = float(confidence_pct) / 100.0
        gl_no_str = str(gl_no_int)
        needs_review = confidence < threshold or gl_no_int == 5900
        result.append(CategorizedTransaction(
            **txn.model_dump(),
            gl_account_no=gl_no_str,
            gl_account_name=gl_name,
            category=gl_name,
            confidence=confidence,
            matched_rule_id=f"concetta-gl{gl_no_str}",
            needs_review=needs_review,
        ))
    return result
