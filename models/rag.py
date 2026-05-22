"""
RAG (Retrieval-Augmented Generation) data models.

DocumentChunk  — one chunk of a client document stored in vtx_rag.document_chunks
RagChunkResult — one result row returned by a RAG_QUERY search
DocumentType   — vocabulary of indexable document types
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    ENGAGEMENT_LETTER = "engagement_letter"
    T2_RETURN         = "t2_return"
    HST_RETURN        = "hst_return"
    GL_SUMMARY        = "gl_summary"
    BANK_RECON        = "bank_recon"
    CHART_OF_ACCOUNTS = "chart_of_accounts"
    GENERIC           = "generic"


class DocumentChunk(BaseModel):
    """One text chunk of a client document, plus its embedding vector.

    Stored in vtx_rag.document_chunks.
    The `embedding` column maps to FLOAT64 REPEATED in BigQuery.
    """
    chunk_id:      str          = Field(default_factory=lambda: str(uuid.uuid4()))
    client_id:     str                       # e.g. "concetta-enterprises"
    document_type: str                       # DocumentType value
    fiscal_year:   int | None   = None
    fiscal_period: str | None   = None       # e.g. "2025-Q4"
    source_uri:    str | None   = None       # gs:// path to the original document
    chunk_text:    str                       # raw text of this chunk
    chunk_index:   int          = 0          # 0-based position within the document
    embedding:     list[float]  = Field(default_factory=list)
    indexed_at:    datetime     = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class RagChunkResult(BaseModel):
    """One matched chunk returned by a RAG_QUERY search."""
    chunk_id:      str
    client_id:     str
    document_type: str
    fiscal_year:   int | None   = None
    chunk_text:    str
    distance:      float        # cosine distance (lower = more similar)
    source_uri:    str | None   = None
