"""
core/docai_ocr.py
Document AI OCR wrapper.

Processor ID is read from Secret Manager as 'vtx-docai-processor-id'.
Location: us  |  Project: vtx-accounting-os-prod

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


class DocAIOCR:
    """Document AI OCR processor. Credentials and processor name are lazily resolved."""

    def __init__(self) -> None:
        self._client = None
        self._processor_name: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ocr_pdf_bytes(self, pdf_bytes: bytes) -> str:
        """Run Document AI OCR on raw PDF bytes. Returns the full extracted text."""
        from google.cloud import documentai

        request = documentai.ProcessRequest(
            name=self._get_processor_name(),
            raw_document=documentai.RawDocument(
                content=pdf_bytes,
                mime_type="application/pdf",
            ),
        )
        result = self._get_client().process_document(request=request, timeout=900.0)
        return result.document.text

    def ocr_pdf_file(self, path: str | Path) -> str:
        """Read a PDF from disk and run OCR. Returns the full extracted text."""
        return self.ocr_pdf_bytes(Path(path).read_bytes())

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
