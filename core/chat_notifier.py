"""
Google Chat incoming-webhook notifier.

Sends a Cards v2 message to a Google Chat Space when transactions require review.
The webhook URL is stored in Secret Manager (vtx-google-chat-webhook).

Local dev override: set env var VTX_SECRET_VTX_GOOGLE_CHAT_WEBHOOK=https://chat.googleapis.com/...

No OAuth required — incoming webhooks use a signed URL only.

Usage:
    from core.chat_notifier import notify_pending_reviews
    from models.approval import ApprovalItem

    ok = notify_pending_reviews(
        items=pending_items,
        period="2025-12",
        bank_code="RBC",
        account_no="xxxx1234",
        summary={"total": 12, "auto_categorized": 9, "net_movement": "10734.80"},
    )
"""

from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal
from typing import Any

import httpx

SECRET_NAME = "vtx-google-chat-webhook"
BQ_CONSOLE_URL = (
    "https://console.cloud.google.com/bigquery"
    "?project=vtx-accounting-os-prod"
    "&ws=!1m5!1m4!4m3!1svtx-accounting-os-prod!2svtx_accounting!3sapproval_queue"
)


def _webhook_url() -> str | None:
    """Return the webhook URL, or None if not configured."""
    # Env override first (local dev)
    env_key = "VTX_SECRET_" + SECRET_NAME.upper().replace("-", "_")
    if url := os.environ.get(env_key):
        return url
    # Secret Manager
    try:
        from core.secrets import get
        url = get(SECRET_NAME)
        return url if url.startswith("https://") else None
    except Exception:
        return None


def notify_pending_reviews(
    items: list,                # list[ApprovalItem]
    period: str,
    bank_code: str,
    account_no: str,
    summary: dict[str, Any] | None = None,
) -> bool:
    """Post a review-required card to Google Chat. Returns True on success."""
    if not items:
        return True

    url = _webhook_url()
    if not url:
        print(
            f"[chat_notifier] Webhook not configured — skipping notification "
            f"for {len(items)} item(s). Set {SECRET_NAME} in Secret Manager.",
            file=sys.stderr,
        )
        return False

    payload = _build_card(items, period, bank_code, account_no, summary or {})
    try:
        resp = httpx.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as exc:
        print(f"[chat_notifier] Webhook POST failed: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Card builder
# ---------------------------------------------------------------------------

def _fmt_amount(amount: Any) -> str:
    amt = Decimal(str(amount))
    sign = "+" if amt >= 0 else ""
    return f"CAD {sign}${abs(amt):,.2f}"


def _build_card(
    items: list,
    period: str,
    bank_code: str,
    account_no: str,
    summary: dict[str, Any],
) -> dict:
    count = len(items)
    title = f"{count} Transaction{'s' if count != 1 else ''} Require Review"
    subtitle = f"{bank_code} {account_no} | {period}"

    # Summary section widgets
    summary_widgets: list[dict] = []
    if "total_transactions" in summary:
        summary_widgets.append(_decorated("Total transactions",   str(summary["total_transactions"])))
        summary_widgets.append(_decorated("Auto-categorized",     str(summary["auto_categorized"])))
        summary_widgets.append(_decorated("Needs review",         str(summary["needs_review"]), bold=True))
    if "net_movement" in summary:
        summary_widgets.append(_decorated("Net movement", _fmt_amount(summary["net_movement"])))

    # Transaction list widgets (cap at 10 in card, rest visible in BQ)
    txn_widgets: list[dict] = []
    for item in items[:10]:
        date_str = item.txn_date.strftime("%b %d, %Y") if hasattr(item.txn_date, "strftime") else str(item.txn_date)
        amount_str = _fmt_amount(item.amount)
        confidence_pct = f"{int(item.confidence * 100)}%"
        txn_widgets.append({
            "decoratedText": {
                "topLabel": f"{date_str} | {amount_str}",
                "text": f"<b>{item.description}</b>",
                "bottomLabel": (
                    f"Suggested: {item.suggested_gl_no} {item.suggested_gl_name}"
                    f" | Confidence: {confidence_pct}"
                ),
                "startIcon": {"knownIcon": "DOLLAR"},
            }
        })

    if len(items) > 10:
        txn_widgets.append({
            "textParagraph": {
                "text": f"<i>... and {len(items) - 10} more. Open BigQuery to see all.</i>"
            }
        })

    sections = []
    if summary_widgets:
        sections.append({"header": "Statement Summary", "widgets": summary_widgets})

    sections.append({
        "header": f"Pending Transactions ({count})",
        "collapsible": count > 3,
        "uncollapsibleWidgetsCount": min(3, count),
        "widgets": txn_widgets,
    })

    sections.append({
        "widgets": [{
            "buttonList": {
                "buttons": [{
                    "text": "Open Review Queue in BigQuery",
                    "onClick": {"openLink": {"url": BQ_CONSOLE_URL}},
                }]
            }
        }]
    })

    return {
        "cardsV2": [{
            "cardId": f"vtx-review-{uuid.uuid4().hex[:8]}",
            "card": {
                "header": {
                    "title": title,
                    "subtitle": subtitle,
                    "imageUrl": (
                        "https://fonts.gstatic.com/s/i/short-term/release/"
                        "materialsymbolsoutlined/pending_actions/default/48px.svg"
                    ),
                    "imageType": "CIRCLE",
                },
                "sections": sections,
            },
        }]
    }


def _decorated(label: str, text: str, bold: bool = False) -> dict:
    display = f"<b>{text}</b>" if bold else text
    return {"decoratedText": {"topLabel": label, "text": display}}
