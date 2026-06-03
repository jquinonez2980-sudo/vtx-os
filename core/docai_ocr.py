"""
core/docai_ocr.py
Document AI OCR wrapper.

Processor ID is read from Secret Manager as 'vtx-docai-processor-id'.
Location: us  |  Project: vtx-accounting-os-prod

Routing:
    < 5 MB  → synchronous process_document  (low latency, 300 s limit)
    ≥ 5 MB  → async batch_process_documents via GCS (no size/time cap)

Usage (instance):
    from core.docai_ocr import DocAIOCR
    ocr  = DocAIOCR()
    text = ocr.ocr_pdf_file("data/statement.pdf")
    text = ocr.ocr_pdf_bytes(pdf_bytes)

Usage (module-level convenience):
    from core.docai_ocr import ocr_pdf_file, ocr_pdf_bytes
    text = ocr_pdf_file("data/statement.pdf")
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

DOCAI_LOCATION   = "us"
DOCAI_PROJECT    = os.environ.get("GOOGLE_CLOUD_PROJECT", "vtx-accounting-os-prod")
PROCESSOR_SECRET = "vtx-docai-processor-id"

_GCS_BUCKET      = "vtx-accounting-os-prod-vtx-exports"
_SYNC_LIMIT      = 5 * 1024 * 1024   # 5 MB — sync below this
_SYNC_TIMEOUT    = 300.0              # seconds
_BATCH_TIMEOUT   = 900               # seconds — LRO wait ceiling


def _page_texts_from_doc(doc: dict) -> list[str]:
    """Row-ordered text for each page from Document AI output, returned as a list.

    Document AI sometimes reads multi-column tables column-by-column rather
    than row-by-row, producing description text separated from
    amounts/dates/balances.  Using bounding-box Y-coordinates to re-sort
    visual lines into proper rows gives DESCRIPTION  AMOUNT  DATE  BALANCE
    on a single line — the format bank_statement_ocr_parser expects.

    Returns one reconstructed string per page (empty list when doc has no text).
    Use _reconstruct_row_ordered_text() when a single joined string is needed.
    """
    full_text = doc.get("text", "")
    if not full_text:
        return []

    page_texts: list[str] = []
    for page in doc.get("pages", []):
        items: list[tuple[float, float, str]] = []  # (center_y, center_x, text)

        for line in page.get("lines", []):
            layout = line.get("layout", {})

            # Extract text via textAnchor segments into the global text field
            line_text = ""
            for seg in layout.get("textAnchor", {}).get("textSegments", []):
                s = int(seg.get("startIndex", 0))
                e = int(seg.get("endIndex", 0))
                if 0 <= s < e <= len(full_text):
                    line_text += full_text[s:e]
            line_text = line_text.strip().rstrip("\n").strip()
            if not line_text:
                continue

            # Bounding box centre
            verts = layout.get("boundingPoly", {}).get("normalizedVertices", [])
            if verts:
                xs = [v.get("x", 0.0) for v in verts]
                ys = [v.get("y", 0.0) for v in verts]
                cx = (min(xs) + max(xs)) / 2.0
                cy = (min(ys) + max(ys)) / 2.0
            else:
                cx, cy = 0.0, 0.0

            items.append((cy, cx, line_text))

        if not items:
            continue

        # Sort by (Y, X) — top-to-bottom then left-to-right
        items.sort(key=lambda t: (t[0], t[1]))

        # Group lines whose centres are within ~0.4 % of page height into the same row.
        # Empirically measured on TD Bank scanned statements:
        #   within-row Y variation: ≤ 0.0017   (elements on the same printed line)
        #   between-row Y gap:      ≥ 0.009     (adjacent transaction rows)
        # 0.004 is comfortably above within-row noise and well below between-row gaps.
        ROW_TOL = 0.004
        rows: list[list[tuple[float, float, str]]] = [[items[0]]]
        for item in items[1:]:
            if abs(item[0] - rows[-1][0][0]) <= ROW_TOL:
                rows[-1].append(item)
            else:
                rows.append([item])

        # Concatenate each row left-to-right with two spaces as column separator
        page_line = "\n".join(
            "  ".join(t[2] for t in sorted(row, key=lambda t: t[1]))
            for row in rows
        )
        page_texts.append(page_line)

    return page_texts


def _reconstruct_row_ordered_text(doc: dict) -> str:
    """Backward-compatible wrapper: join _page_texts_from_doc into a single string."""
    full_text = doc.get("text", "")
    pages = _page_texts_from_doc(doc)
    return "\n".join(pages) if pages else full_text


class DocAIOCR:
    """Document AI OCR processor. Credentials and processor name are lazily resolved."""

    def __init__(self) -> None:
        self._client = None
        self._processor_name: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ocr_pdf_bytes(self, pdf_bytes: bytes) -> str:
        """OCR raw PDF bytes. Routes to sync or batch based on size."""
        text, _ = self.ocr_pdf_bytes_with_pages(pdf_bytes)
        return text

    def ocr_pdf_file(self, path: str | Path) -> str:
        """Read a PDF from disk and OCR it."""
        return self.ocr_pdf_bytes(Path(path).read_bytes())

    def ocr_pdf_bytes_with_pages(self, pdf_bytes: bytes) -> tuple[str, list[str]]:
        """OCR raw PDF bytes; returns (joined_text, per_page_texts)."""
        if len(pdf_bytes) >= _SYNC_LIMIT:
            return self._ocr_batch(pdf_bytes)
        return self._ocr_sync(pdf_bytes)

    def ocr_pdf_file_with_pages(self, path: str | Path) -> tuple[str, list[str]]:
        """Read a PDF from disk and OCR it; returns (joined_text, per_page_texts)."""
        return self.ocr_pdf_bytes_with_pages(Path(path).read_bytes())

    # ------------------------------------------------------------------
    # Sync path (< 5 MB)
    # ------------------------------------------------------------------

    def _ocr_sync(self, pdf_bytes: bytes) -> tuple[str, list[str]]:
        import json
        from google.cloud import documentai
        request = documentai.ProcessRequest(
            name=self._get_processor_name(),
            raw_document=documentai.RawDocument(
                content=pdf_bytes,
                mime_type="application/pdf",
            ),
        )
        result = self._get_client().process_document(
            request=request, timeout=_SYNC_TIMEOUT
        )
        doc = result.document

        # Apply the same bounding-box row reconstruction as the batch path.
        # Without it, multi-column statements (e.g. TD Bank) come back read
        # column-by-column — descriptions split from their amounts/dates — and
        # bank_statement_ocr_parser parses 0 transactions. to_json() emits the
        # camelCase field names _page_texts_from_doc expects.
        try:
            doc_dict = json.loads(documentai.Document.to_json(doc))
            page_texts = _page_texts_from_doc(doc_dict)
            if page_texts:
                return "\n".join(page_texts), page_texts
        except Exception as exc:
            log.warning("sync row reconstruction failed, using raw text: %s", exc)

        return doc.text, [doc.text]

    # ------------------------------------------------------------------
    # Batch/async path (≥ 5 MB)
    # ------------------------------------------------------------------

    def _ocr_batch(self, pdf_bytes: bytes) -> tuple[str, list[str]]:
        import json
        import uuid
        from google.cloud import documentai
        from google.cloud import storage as gcs_storage

        run_id          = str(uuid.uuid4())
        input_blob_name = f"docai-tmp/{run_id}/input.pdf"
        output_prefix   = f"docai-tmp/{run_id}/output/"
        gcs_input_uri   = f"gs://{_GCS_BUCKET}/{input_blob_name}"
        gcs_output_uri  = f"gs://{_GCS_BUCKET}/{output_prefix}"

        storage_client = gcs_storage.Client(project=DOCAI_PROJECT)
        bucket = storage_client.bucket(_GCS_BUCKET)

        # 1 — Upload PDF to a temporary GCS location.
        # Use upload_from_file (resumable for > 8 MB).
        # timeout=600 is the per-request timeout; retry deadline is set to 660 s
        # to avoid the default 120 s api-core retry ceiling on large uploads.
        import io
        from google.api_core import retry as api_retry
        _upload_retry = api_retry.Retry(deadline=660)
        bucket.blob(input_blob_name).upload_from_file(
            io.BytesIO(pdf_bytes),
            content_type="application/pdf",
            size=len(pdf_bytes),
            timeout=600,
            retry=_upload_retry,
        )

        try:
            # 2 — Kick off the batch job
            operation = self._get_client().batch_process_documents(
                request=documentai.BatchProcessRequest(
                    name=self._get_processor_name(),
                    input_documents=documentai.BatchDocumentsInputConfig(
                        gcs_documents=documentai.GcsDocuments(
                            documents=[documentai.GcsDocument(
                                gcs_uri=gcs_input_uri,
                                mime_type="application/pdf",
                            )]
                        )
                    ),
                    document_output_config=documentai.DocumentOutputConfig(
                        gcs_output_config=documentai.DocumentOutputConfig.GcsOutputConfig(
                            gcs_uri=gcs_output_uri,
                        )
                    ),
                )
            )

            # 3 — Wait for LRO completion
            operation.result(timeout=_BATCH_TIMEOUT)

            # 4 — Read output shards from GCS (sorted for correct page order)
            texts: list[str] = []
            all_page_texts: list[str] = []
            output_blobs = sorted(
                bucket.list_blobs(prefix=output_prefix),
                key=lambda b: b.name,
            )
            for blob in output_blobs:
                if blob.name.endswith(".json"):
                    doc = json.loads(blob.download_as_text())
                    shard_pages = _page_texts_from_doc(doc)
                    if shard_pages:
                        all_page_texts.extend(shard_pages)
                        texts.append("\n".join(shard_pages))
                    else:
                        fallback = doc.get("text") or ""
                        if fallback:
                            texts.append(fallback)

            return "\n".join(texts), all_page_texts

        finally:
            # 5 — Best-effort cleanup of temp GCS files
            try:
                bucket.blob(input_blob_name).delete()
            except Exception:
                pass
            try:
                for blob in list(bucket.list_blobs(prefix=output_prefix)):
                    blob.delete()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_processor_name(self) -> str:
        if self._processor_name is None:
            from core.secrets import get
            processor_id = get(PROCESSOR_SECRET)
            self._processor_name = (
                f"projects/{DOCAI_PROJECT}/locations/{DOCAI_LOCATION}"
                f"/processors/{processor_id.strip()}"
            )
        return self._processor_name

    def _get_client(self):
        if self._client is None:
            from google.cloud import documentai
            from google.api_core.client_options import ClientOptions
            self._client = documentai.DocumentProcessorServiceClient(
                client_options=ClientOptions(
                    api_endpoint=f"{DOCAI_LOCATION}-documentai.googleapis.com"
                )
            )
        return self._client


# ---------------------------------------------------------------------------
# Module-level convenience — shares a single instance per process
# ---------------------------------------------------------------------------

_default: DocAIOCR | None = None


def _instance() -> DocAIOCR:
    global _default
    if _default is None:
        _default = DocAIOCR()
    return _default


def ocr_pdf_bytes(pdf_bytes: bytes) -> str:
    """OCR raw PDF bytes using the default DocAIOCR instance."""
    return _instance().ocr_pdf_bytes(pdf_bytes)


def ocr_pdf_file(path: str | Path) -> str:
    """OCR a PDF file using the default DocAIOCR instance."""
    return _instance().ocr_pdf_file(path)


def ocr_pdf_file_with_pages(path: str | Path) -> tuple[str, list[str]]:
    """OCR a PDF file; returns (joined_text, per_page_texts)."""
    return _instance().ocr_pdf_file_with_pages(path)
