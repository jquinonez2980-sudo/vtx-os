"""
core/gmail_notifier.py
Standalone Gmail client using vtx-gmail-oauth-credentials from Secret Manager.

Self-contained — does not import from agents/gmail_comms.

Usage:
    from core.gmail_notifier import GmailNotifier

    n = GmailNotifier()
    profile = n.get_profile()
    msgs    = n.poll_for_pdf_attachments()
    path    = n.save_attachment(msg["msg_id"], att["attachment_id"],
                                att["filename"], dest_dir)
    n.send_message(to="client@example.com", subject="Close", body="...")
    n.mark_read(msg_id)
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
from pathlib import Path
from typing import Any

CREDENTIAL_SECRET = "vtx-gmail-oauth-credentials"
FROM_EMAIL = "jquinonez2980@gmail.com"
_DEFAULT_QUERY = "is:unread has:attachment (filename:pdf OR filename:csv) in:inbox"
_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


class GmailNotifier:
    """Thin, lazily-authenticated wrapper around Gmail API v1."""

    def __init__(self) -> None:
        self._service: Any | None = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _svc(self) -> Any:
        if self._service is None:
            self._service = _build_service(_load_creds_json())
        return self._service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_profile(self) -> dict:
        """Return the authenticated user's Gmail profile dict.

        Keys: emailAddress, messagesTotal, threadsTotal, historyId
        """
        return self._svc().users().getProfile(userId="me").execute()

    def send_message(
        self,
        to: str | list[str],
        subject: str,
        body: str,
        *,
        cc: str | list[str] | None = None,
        html_body: str | None = None,
        thread_id: str | None = None,
    ) -> dict:
        """Send an email and return {'message_id': ..., 'thread_id': ...}."""
        to_str = to if isinstance(to, str) else ", ".join(to)
        cc_str = (cc if isinstance(cc, str) else ", ".join(cc)) if cc else None
        raw = _build_raw_message(
            from_addr=FROM_EMAIL,
            to=to_str,
            subject=subject,
            body=body,
            cc=cc_str,
            html_body=html_body,
        )
        payload: dict = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id
        sent = self._svc().users().messages().send(userId="me", body=payload).execute()
        return {"message_id": sent.get("id", ""), "thread_id": sent.get("threadId", "")}

    def poll_for_pdf_attachments(
        self,
        query: str = _DEFAULT_QUERY,
        max_results: int = 20,
    ) -> list[dict]:
        """Return metadata for inbox messages that have PDF attachments.

        Each dict:
            msg_id       str
            thread_id    str
            subject      str
            from         str
            epoch_ms     int
            attachments  list[dict]  — each: {attachment_id, filename, size}
        """
        resp = (
            self._svc()
            .users()
            .messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        out = []
        for m in resp.get("messages", []):
            info = self._message_pdf_info(m["id"])
            if info and info["attachments"]:
                out.append(info)
        return out

    def save_attachment(
        self,
        msg_id: str,
        attachment_id: str,
        filename: str,
        dest_dir: Path,
    ) -> Path:
        """Download one Gmail attachment by ID and write it to dest_dir.

        Returns the path of the saved file.
        """
        att = (
            self._svc()
            .users()
            .messages()
            .attachments()
            .get(userId="me", messageId=msg_id, id=attachment_id)
            .execute()
        )
        raw = base64.urlsafe_b64decode(att["data"])
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / _safe_filename(filename)
        dest.write_bytes(raw)
        return dest

    def mark_read(self, msg_id: str, label_name: str = "vtx-processed") -> None:
        """Remove UNREAD label and apply *label_name* (created if absent)."""
        label_id = self._get_or_create_label(label_name)
        self._svc().users().messages().modify(
            userId="me",
            id=msg_id,
            body={"removeLabelIds": ["UNREAD"], "addLabelIds": [label_id]},
        ).execute()

    def apply_label(self, msg_id: str, label_name: str) -> None:
        """Apply *label_name* (created if absent) WITHOUT marking the message read.

        Used to quarantine unrouted statements: the email stays unread so it is
        retried on the next poll once a mapping is added.
        """
        label_id = self._get_or_create_label(label_name)
        self._svc().users().messages().modify(
            userId="me",
            id=msg_id,
            body={"addLabelIds": [label_id]},
        ).execute()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _message_pdf_info(self, msg_id: str) -> dict | None:
        try:
            msg = (
                self._svc()
                .users()
                .messages()
                .get(userId="me", id=msg_id, format="full")
                .execute()
            )
        except Exception:
            return None

        headers = {
            h["name"]: h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }
        attachments: list[dict] = []
        _walk_parts(msg.get("payload", {}), attachments)
        if not attachments:
            return None

        return {
            "msg_id":      msg_id,
            "thread_id":   msg.get("threadId", ""),
            "subject":     headers.get("Subject", ""),
            "from":        headers.get("From", ""),
            "epoch_ms":    int(msg.get("internalDate", 0)),
            "attachments": attachments,
        }

    def _get_or_create_label(self, name: str) -> str:
        labels = (
            self._svc().users().labels().list(userId="me").execute().get("labels", [])
        )
        for lbl in labels:
            if lbl["name"] == name:
                return lbl["id"]
        created = self._svc().users().labels().create(
            userId="me",
            body={
                "name": name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        ).execute()
        return created["id"]


# ---------------------------------------------------------------------------
# Module-level private helpers
# ---------------------------------------------------------------------------

def _load_creds_json() -> str:
    """Return raw credential JSON string from Secret Manager (or env override)."""
    from core.secrets import get
    return get(CREDENTIAL_SECRET)


def _build_service(creds_json: str) -> Any:
    """Build an authenticated Gmail API service from a credential JSON string."""
    import json
    import google.oauth2.credentials
    from googleapiclient.discovery import build

    info = json.loads(creds_json)
    creds = google.oauth2.credentials.Credentials.from_authorized_user_info(
        info, scopes=_SCOPES
    )
    return build("gmail", "v1", credentials=creds)


def _build_raw_message(
    from_addr: str,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    html_body: str | None = None,
) -> str:
    """Build and return a base64url-encoded RFC 2822 message for the Gmail API."""
    msg = EmailMessage()
    msg["From"]    = from_addr
    msg["To"]      = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if html_body:
        msg.set_content(body)
        msg.add_alternative(html_body, subtype="html")
    else:
        msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _walk_parts(part: dict, results: list[dict]) -> None:
    """Recursively collect PDF and CSV attachment metadata from a MIME part tree."""
    if "parts" in part:
        for p in part["parts"]:
            _walk_parts(p, results)
        return
    mime  = part.get("mimeType", "")
    fname = part.get("filename", "")
    fname_lower = fname.lower()
    is_pdf = mime == "application/pdf" or fname_lower.endswith(".pdf")
    is_csv = mime in ("text/csv", "application/csv") or fname_lower.endswith(".csv")
    if is_pdf or is_csv:
        att_id = part.get("body", {}).get("attachmentId")
        size   = part.get("body", {}).get("size", 0)
        if att_id:
            results.append({
                "attachment_id": att_id,
                "filename":      fname or ("attachment.csv" if is_csv else "attachment.pdf"),
                "size":          size,
            })


def _safe_filename(name: str) -> str:
    import re
    return re.sub(r"[^\w.\-]", "_", name) or "attachment.pdf"
