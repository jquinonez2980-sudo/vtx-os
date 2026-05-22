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

import os
from pathlib import Path

DOCAI_LOCATION   = "us"
DOCAI_PROJECT    = os.environ.get("GOOGLE_CLOUD_PROJECT", "vtx-accounting-os-prod")
PROCESSOR_SECRET = "vtx-docai-processor-id"

_GCS_BUCKET      = "vtx-accounting-os-prod-vtx-exports"
_SYNC_LIMIT      = 5 * 1024 * 1024   # 5 MB — sync below this
_SYNC_TIMEOUT    = 300.0              # seconds
_BATCH_TIMEOUT   = 900               # seconds — LRO wait ceiling


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
        if len(pdf_bytes) >= _SYNC_LIMIT:
            return self._ocr_batch(pdf_bytes)
        return self._ocr_sync(pdf_bytes)

    def ocr_pdf_file(self, path: str | Path) -> str:
        """Read a PDF from disk and OCR it."""
        return self.ocr_pdf_bytes(Path(path).read_bytes())

    # ------------------------------------------------------------------
    # Sync path (< 5 MB)
    # ------------------------------------------------------------------

    def _ocr_sync(self, pdf_bytes: bytes) -> str:
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
        return result.document.text

    # ------------------------------------------------------------------
    # Batch/async path (≥ 5 MB)
    # ------------------------------------------------------------------

    def _ocr_batch(self, pdf_bytes: bytes) -> str:
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
        # Use upload_from_file (resumable for > 8 MB) with a generous timeout.
        import io
        bucket.blob(input_blob_name).upload_from_file(
            io.BytesIO(pdf_bytes),
            content_type="application/pdf",
            size=len(pdf_bytes),
            timeout=600,
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
            output_blobs = sorted(
                bucket.list_blobs(prefix=output_prefix),
                key=lambda b: b.name,
            )
            for blob in output_blobs:
                if blob.name.endswith(".json"):
                    doc = json.loads(blob.download_as_text())
                    text = doc.get("text") or ""
                    if text:
                        texts.append(text)

            return "\n".join(texts)

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
