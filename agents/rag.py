"""
RagAgent — index client documents and answer queries via semantic similarity.

Handles two task types on the same agent_id ("rag-agent"):

INDEX_DOCUMENT payload:
    document_type  (str, required)  — DocumentType value (e.g. "gl_summary")
    client_id      (str, required)  — client slug, e.g. "concetta-enterprises"
    source_text    (str, required)  — full document text; chunked + embedded here
    fiscal_year    (int, optional)  — e.g. 2025
    fiscal_period  (str, optional)  — e.g. "2025-Q4"
    source_uri     (str, optional)  — gs:// origin of the document
    chunk_size     (int, optional)  — chars per chunk; default 1000
    chunk_overlap  (int, optional)  — overlap between consecutive chunks; default 100

INDEX_DOCUMENT returns:
    chunks_indexed   — number of chunks stored to BQ
    document_type    — echoed
    client_id        — echoed

RAG_QUERY payload:
    query          (str, required)  — natural-language question
    client_id      (str, optional)  — filter to one client
    document_type  (str, optional)  — filter to one document type
    fiscal_year    (int, optional)  — filter to one fiscal year
    top_k          (int, optional)  — result chunks to return; default 5

RAG_QUERY returns:
    query          — echoed
    chunks         — list of {chunk_id, client_id, document_type, fiscal_year,
                              chunk_text, distance, source_uri}
    context        — chunks concatenated with separators (paste into LLM prompt)
    total_found    — number of chunks returned

Storage:
    BQ dataset: vtx_rag
    BQ table:   document_chunks  (FLOAT64 REPEATED embedding column)

Embedding model: text-embedding-005 (google-genai SDK, Vertex AI backend;
                 configurable via VTX_EMBEDDING_MODEL)
"""

from __future__ import annotations

import os
from typing import Any

from agents.base import AgentBase, TaskRequest, TaskResult, TaskType
from models.base import EventStatus
from models.rag import DocumentChunk

DATASET      = "vtx_rag"
TABLE        = "document_chunks"
_EMBED_MODEL = os.environ.get("VTX_EMBEDDING_MODEL", "text-embedding-005")
_EMBED_BATCH = 250          # max texts per embedding request

# Lazily-created google-genai client (Vertex AI backend). Tests inject a mock by
# setting agents.rag._genai_client directly, mirroring core.bq_loader._client.
_genai_client = None


class RagAgent(AgentBase):
    agent_id = "rag-agent"

    def handle(self, request: TaskRequest) -> TaskResult:
        if request.task_type == TaskType.INDEX_DOCUMENT:
            return self._handle_index(request)
        if request.task_type == TaskType.RAG_QUERY:
            return self._handle_query(request)
        return TaskResult(
            task_id=request.task_id,
            task_type=request.task_type,
            agent_id=self.agent_id,
            status=EventStatus.FAILURE,
            error=f"RagAgent does not handle task type '{request.task_type.value}'",
        )

    # ------------------------------------------------------------------
    # INDEX_DOCUMENT
    # ------------------------------------------------------------------

    def _handle_index(self, request: TaskRequest) -> TaskResult:
        from core.bq_loader import ensure_dataset, ensure_table, load_rows

        payload       = request.payload
        source_text   = payload.get("source_text", "")
        if not source_text.strip():
            return TaskResult(
                task_id=request.task_id,
                task_type=request.task_type,
                agent_id=self.agent_id,
                status=EventStatus.FAILURE,
                error="source_text is required and must not be empty",
            )

        client_id     = payload["client_id"]
        document_type = payload["document_type"]
        fiscal_year   = payload.get("fiscal_year")
        fiscal_period = payload.get("fiscal_period")
        source_uri    = payload.get("source_uri")
        chunk_size    = int(payload.get("chunk_size", 1000))
        chunk_overlap = int(payload.get("chunk_overlap", 100))

        # 1. Chunk the text
        texts = _chunk_text(source_text, chunk_size, chunk_overlap)

        # 2. Embed all chunks (batched)
        vectors = _embed_texts(texts)

        # 3. Build DocumentChunk rows
        chunks: list[DocumentChunk] = [
            DocumentChunk(
                client_id=client_id,
                document_type=document_type,
                fiscal_year=fiscal_year,
                fiscal_period=fiscal_period,
                source_uri=source_uri,
                chunk_text=text,
                chunk_index=i,
                embedding=vector,
            )
            for i, (text, vector) in enumerate(zip(texts, vectors))
        ]

        # 4. Ensure dataset + table, then stream rows
        ensure_dataset(DATASET)
        ensure_table(DATASET, TABLE, DocumentChunk,
                     cluster_fields=["client_id", "document_type"])
        load_rows(DATASET, TABLE, chunks, session_id=request.session_id)

        return TaskResult(
            task_id=request.task_id,
            task_type=request.task_type,
            agent_id=self.agent_id,
            status=EventStatus.SUCCESS,
            output={
                "chunks_indexed": len(chunks),
                "document_type":  document_type,
                "client_id":      client_id,
            },
        )

    # ------------------------------------------------------------------
    # RAG_QUERY
    # ------------------------------------------------------------------

    def _handle_query(self, request: TaskRequest) -> TaskResult:
        payload       = request.payload
        query_text    = payload.get("query", "")
        if not query_text.strip():
            return TaskResult(
                task_id=request.task_id,
                task_type=request.task_type,
                agent_id=self.agent_id,
                status=EventStatus.FAILURE,
                error="query is required and must not be empty",
            )

        top_k         = int(payload.get("top_k", 5))
        client_id     = payload.get("client_id")
        document_type = payload.get("document_type")
        fiscal_year   = payload.get("fiscal_year")

        # 1. Embed the query
        query_vec = _embed_texts([query_text])[0]

        # 2. Build filters
        filters: dict[str, Any] = {}
        if client_id:     filters["client_id"]     = client_id
        if document_type: filters["document_type"] = document_type
        if fiscal_year:   filters["fiscal_year"]   = int(fiscal_year)

        # 3. Vector search in BQ
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "vtx-accounting-os-prod")
        table_id = f"{project}.{DATASET}.{TABLE}"
        raw_rows = _vector_search(table_id, query_vec, filters, top_k)

        # 4. Build context string for downstream LLM prompts
        context_parts = []
        for row in raw_rows:
            header = f"[{row.get('document_type', '')} | {row.get('client_id', '')}]"
            context_parts.append(f"{header}\n{row.get('chunk_text', '')}")
        context = "\n\n---\n\n".join(context_parts)

        return TaskResult(
            task_id=request.task_id,
            task_type=request.task_type,
            agent_id=self.agent_id,
            status=EventStatus.SUCCESS,
            output={
                "query":       query_text,
                "chunks":      raw_rows,
                "context":     context,
                "total_found": len(raw_rows),
            },
        )


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _chunk_text(text: str, chunk_size: int = 1000, overlap: int = 100) -> list[str]:
    """Sliding-window character chunker. Returns at least one chunk."""
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return chunks


# ---------------------------------------------------------------------------
# Embedding (text-embedding-005 via the google-genai SDK, Vertex AI backend)
# ---------------------------------------------------------------------------
# Migrated off vertexai.language_models.TextEmbeddingModel.from_pretrained, which
# is deprecated and slated for removal (June 2026). The google-genai SDK is the
# supported path going forward; same model, same returned vectors.

def _client():
    """Return a cached google-genai client bound to the Vertex AI backend."""
    global _genai_client
    if _genai_client is None:
        from google import genai
        _genai_client = genai.Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT", "vtx-accounting-os-prod"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "northamerica-northeast1"),
        )
    return _genai_client


def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a list of strings via google-genai.  Returns one vector per text."""
    if not texts:
        return []
    client = _client()

    vectors: list[list[float]] = []
    for i in range(0, len(texts), _EMBED_BATCH):
        batch = texts[i : i + _EMBED_BATCH]
        response = client.models.embed_content(model=_EMBED_MODEL, contents=batch)
        vectors.extend(list(e.values) for e in response.embeddings)
    return vectors


# ---------------------------------------------------------------------------
# BQ VECTOR_SEARCH
# ---------------------------------------------------------------------------

def _vector_search(
    table_id: str,
    query_embedding: list[float],
    filters: dict[str, Any],
    top_k: int,
) -> list[dict]:
    """Run BQ VECTOR_SEARCH and return rows as plain dicts."""
    from google.cloud import bigquery as bq_lib
    from core.bq_loader import _bq

    params: list = [
        bq_lib.ArrayQueryParameter("query_vec", "FLOAT64", query_embedding),
    ]

    where_conditions: list[str] = []
    for key, value in filters.items():
        where_conditions.append(f"base.{key} = @{key}")
        if isinstance(value, int):
            params.append(bq_lib.ScalarQueryParameter(key, "INT64", value))
        else:
            params.append(bq_lib.ScalarQueryParameter(key, "STRING", str(value)))

    where_filter = ("WHERE " + " AND ".join(where_conditions)) if where_conditions else ""

    sql = f"""
    SELECT base.chunk_id, base.client_id, base.document_type, base.chunk_text,
           base.fiscal_year, base.fiscal_period, base.source_uri, distance
    FROM VECTOR_SEARCH(
        TABLE `{table_id}`,
        'embedding',
        (SELECT @query_vec AS embedding),
        top_k => {top_k},
        distance_type => 'COSINE'
    )
    {where_filter}
    ORDER BY distance ASC
    """

    job_config = bq_lib.QueryJobConfig(query_parameters=params)
    rows = list(_bq().query(sql, job_config=job_config).result())
    return [{k: row[k] for k in row.keys()} for row in rows]
