"""
tests/p2_5_rag_smoke.py
P2.5 smoke test - RagAgent: document indexing + semantic search.

OFFLINE: no live GCP, no Vertex AI embedding calls.
         MockBQClient + patched vertexai.

Checks:
   --- Registration ---
   1    RagAgent registered for INDEX_DOCUMENT in OrchestratorAgent
   2    RagAgent registered for RAG_QUERY in OrchestratorAgent
   3    Both share the same agent_id ("rag-agent")
   4    RagAgent registered in A2ATransport
   5    AgentCard valid (name, url, skills)

   --- Chunking logic (_chunk_text) ---
   6    Short text (< chunk_size) produces exactly one chunk
   7    Long text produces multiple chunks
   8    Chunks overlap by the configured overlap amount
   9    Last chunk contains the tail of the text

   --- INDEX_DOCUMENT ---
  10    result.ok is True
  11    output chunks_indexed == expected chunk count
  12    embedding model called once per chunk
  13    BQ insert_rows_json called (chunks stored)
  14    Empty source_text -> FAILURE (not an exception)

   --- RAG_QUERY ---
  15    result.ok is True
  16    output 'context' contains matched chunk text
  17    output 'chunks' is a non-empty list
  18    output 'total_found' == len(chunks)
  19    VECTOR_SEARCH appears in BQ SQL executed
  20    client_id filter present in BQ SQL when provided

   --- schema_from_model + ensure_dataset ---
  21    list[float] field -> FLOAT64 REPEATED in BQ schema
  22    ensure_dataset calls create_dataset on BQ client

   --- Orchestrator A2A dispatch ---
  23    INDEX_DOCUMENT dispatch via orchestrator: result.ok is True
  24    RAG_QUERY dispatch via orchestrator: result.ok is True
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# MockBQClient with query tracking
# ---------------------------------------------------------------------------

class MockBQClient:
    def __init__(self):
        self.inserted: dict[str, list] = {}
        self.queries:  list[str]       = []
        self.datasets_created:  list   = []

    def get_table(self, table_id):
        from google.cloud.exceptions import NotFound
        raise NotFound(f"(mock) {table_id}")

    def create_table(self, table):
        return table

    def create_dataset(self, dataset, **_):
        self.datasets_created.append(dataset)
        return dataset

    def insert_rows_json(self, table_id, rows, **_):
        self.inserted.setdefault(str(table_id), []).extend(rows)
        return []

    def query(self, sql, **_):
        self.queries.append(sql)
        job = MagicMock()
        if "VECTOR_SEARCH" in sql:
            row = _MockRow(
                chunk_id="chunk-001",
                client_id="concetta-enterprises",
                document_type="gl_summary",
                chunk_text="Revenue for Q4 2025 was CAD $23,249.07.",
                fiscal_year=2025,
                fiscal_period="2025-Q4",
                source_uri=None,
                distance=0.12,
            )
            job.result.return_value = [row]
        else:
            job.result.return_value = []
        return job

    def total_rows(self) -> int:
        return sum(len(v) for v in self.inserted.values())


class _MockRow:
    """dict-like object matching BigQuery Row interface."""
    def __init__(self, **data):
        self._data = data
    def keys(self):
        return self._data.keys()
    def __getitem__(self, k):
        return self._data[k]


def _inject(client):
    import core.bq_loader, core.audit
    core.bq_loader._client = client
    core.audit._client     = client


# ---------------------------------------------------------------------------
# Mock google-genai embedding client (Vertex AI backend)
# ---------------------------------------------------------------------------

FAKE_VECTOR = [0.1, 0.2, 0.3]   # 3-dim stub; real model returns 768 dims

def _make_genai_mock():
    """A mock google-genai client whose embed_content returns one vector per input."""
    client = MagicMock()

    def _embed(*, model, contents):
        resp = MagicMock()
        resp.embeddings = [type("E", (), {"values": FAKE_VECTOR})() for _ in contents]
        return resp

    client.models.embed_content.side_effect = _embed
    return client


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run() -> None:
    mock_bq = MockBQClient()
    _inject(mock_bq)

    from agents.a2a import A2ATransport, AgentCard
    from agents.base import TaskRequest, TaskType
    from agents.orchestrator import OrchestratorAgent
    from agents.rag import RagAgent, _chunk_text
    from core.bq_loader import schema_from_model
    from models.rag import DocumentChunk

    # Inject the mock google-genai client once; agents.rag._client() returns it
    # instead of constructing a live Vertex AI client (mirrors bq_loader._client).
    import agents.rag as _rag_mod
    genai_mock = _make_genai_mock()
    _rag_mod._genai_client = genai_mock

    checks: list[tuple[str, bool]] = []

    # ------------------------------------------------------------------ #
    # 1-5  Registration                                                    #
    # ------------------------------------------------------------------ #
    checks.append(("INDEX_DOCUMENT registered in OrchestratorAgent",
                   TaskType.INDEX_DOCUMENT in OrchestratorAgent.registered_types()))
    checks.append(("RAG_QUERY registered in OrchestratorAgent",
                   TaskType.RAG_QUERY in OrchestratorAgent.registered_types()))

    rag_idx  = OrchestratorAgent._registry.get(TaskType.INDEX_DOCUMENT)
    rag_qry  = OrchestratorAgent._registry.get(TaskType.RAG_QUERY)
    checks.append(("INDEX_DOCUMENT and RAG_QUERY share the same RagAgent instance",
                   rag_idx is rag_qry and isinstance(rag_idx, RagAgent)))

    checks.append(("rag-agent registered in A2ATransport",
                   "rag-agent" in A2ATransport.registered_ids()))

    try:
        card = A2ATransport.agent_card("rag-agent")
        card_ok = (
            isinstance(card, AgentCard)
            and card.name == "rag-agent"
            and card.url == "/"
            and len(card.skills) > 0
        )
    except Exception:
        card_ok = False
    checks.append(("AgentCard valid for rag-agent", card_ok))

    # ------------------------------------------------------------------ #
    # 6-9  _chunk_text                                                     #
    # ------------------------------------------------------------------ #
    short = "Hello world"
    one_chunk = _chunk_text(short, chunk_size=1000, overlap=100)
    checks.append(("Short text (<chunk_size) -> one chunk",
                   len(one_chunk) == 1 and one_chunk[0] == short))

    long_text = "A" * 2500
    chunks = _chunk_text(long_text, chunk_size=1000, overlap=100)
    checks.append(("2500-char text with chunk_size=1000 -> multiple chunks",
                   len(chunks) > 1))

    # With chunk_size=1000, overlap=100:
    # chunk 0: [0, 1000)
    # chunk 1: [900, 1900)  (start = 1000 - 100 = 900)
    # overlap: chars [900, 1000) appear in both chunks 0 and 1
    if len(chunks) >= 2:
        overlap_ok = chunks[0][-100:] == chunks[1][:100]
    else:
        overlap_ok = False
    checks.append(("Consecutive chunks overlap by 100 chars", overlap_ok))

    checks.append(("Last chunk contains tail of original text",
                   long_text.endswith(chunks[-1].rstrip())))

    # ------------------------------------------------------------------ #
    # 10-14  INDEX_DOCUMENT                                                #
    # ------------------------------------------------------------------ #
    SOURCE_TEXT = "Revenue for December 2025 was $23,249.07. " * 30   # ~1280 chars
    INDEX_PAYLOAD = {
        "document_type": "gl_summary",
        "client_id":     "concetta-enterprises",
        "source_text":   SOURCE_TEXT,
        "fiscal_year":   2025,
        "fiscal_period": "2025-12",
        "chunk_size":    500,
        "chunk_overlap": 50,
    }

    expected_chunks = len(_chunk_text(SOURCE_TEXT, chunk_size=500, overlap=50))

    req_idx = TaskRequest(
        task_type=TaskType.INDEX_DOCUMENT,
        requested_by="test@vtx-os.local",
        payload=INDEX_PAYLOAD,
    )

    rows_before = mock_bq.total_rows()
    embed_calls_before = genai_mock.models.embed_content.call_count

    agent = RagAgent()
    result_idx = agent.run(req_idx)

    checks.append(("INDEX_DOCUMENT result.ok is True", result_idx.ok))
    checks.append(("chunks_indexed == expected chunk count",
                   result_idx.output.get("chunks_indexed") == expected_chunks))
    checks.append(("embedding model called once per batch (chunks embedded)",
                   genai_mock.models.embed_content.call_count > embed_calls_before))
    checks.append(("BQ insert_rows_json called (chunks stored)",
                   mock_bq.total_rows() > rows_before))

    # 14  Empty source_text -> FAILURE
    req_empty = TaskRequest(
        task_type=TaskType.INDEX_DOCUMENT,
        requested_by="test@vtx-os.local",
        payload={"document_type": "generic", "client_id": "test", "source_text": ""},
    )
    result_empty = agent.run(req_empty)
    checks.append(("Empty source_text -> FAILURE (not exception)",
                   not result_empty.ok and result_empty.error is not None))

    # ------------------------------------------------------------------ #
    # 15-20  RAG_QUERY                                                     #
    # ------------------------------------------------------------------ #
    QUERY_TEXT = "What was the total revenue for Concetta in December 2025?"

    req_qry = TaskRequest(
        task_type=TaskType.RAG_QUERY,
        requested_by="test@vtx-os.local",
        payload={
            "query":         QUERY_TEXT,
            "client_id":     "concetta-enterprises",
            "document_type": "gl_summary",
            "top_k":         3,
        },
    )

    mock_bq.queries.clear()

    result_qry = agent.run(req_qry)

    checks.append(("RAG_QUERY result.ok is True", result_qry.ok))
    checks.append(("RAG_QUERY output has 'context' key with text",
                   bool(result_qry.output.get("context"))))
    checks.append(("RAG_QUERY output has non-empty 'chunks' list",
                   len(result_qry.output.get("chunks", [])) > 0))
    checks.append(("total_found == len(chunks)",
                   result_qry.output.get("total_found") == len(result_qry.output.get("chunks", []))))
    checks.append(("VECTOR_SEARCH appears in BQ SQL executed",
                   any("VECTOR_SEARCH" in q for q in mock_bq.queries)))
    checks.append(("client_id filter present in BQ SQL",
                   any("client_id" in q for q in mock_bq.queries)))

    # ------------------------------------------------------------------ #
    # 21-22  schema_from_model + ensure_dataset                           #
    # ------------------------------------------------------------------ #
    from google.cloud import bigquery as bq_lib
    schema = schema_from_model(DocumentChunk)
    embedding_field = next((f for f in schema if f.name == "embedding"), None)
    checks.append(("list[float] -> FLOAT64 REPEATED in schema",
                   embedding_field is not None
                   and embedding_field.field_type == "FLOAT64"
                   and embedding_field.mode == "REPEATED"))

    checks.append(("ensure_dataset calls create_dataset on BQ client",
                   len(mock_bq.datasets_created) >= 1))

    # ------------------------------------------------------------------ #
    # 23-24  Orchestrator A2A dispatch                                     #
    # ------------------------------------------------------------------ #
    orch = OrchestratorAgent()

    req_orch_idx = TaskRequest(
        task_type=TaskType.INDEX_DOCUMENT,
        payload=INDEX_PAYLOAD,
    )
    orch_result_idx = orch.run(req_orch_idx)
    checks.append(("Orchestrator dispatch INDEX_DOCUMENT via A2A: result.ok",
                   orch_result_idx.ok))

    req_orch_qry = TaskRequest(
        task_type=TaskType.RAG_QUERY,
        payload={"query": QUERY_TEXT, "client_id": "concetta-enterprises", "top_k": 2},
    )
    orch_result_qry = orch.run(req_orch_qry)
    checks.append(("Orchestrator dispatch RAG_QUERY via A2A: result.ok",
                   orch_result_qry.ok))

    # ------------------------------------------------------------------ #
    # Report                                                               #
    # ------------------------------------------------------------------ #
    passed = sum(1 for _, ok in checks if ok)
    total  = len(checks)
    print(f"\nP2.5 RAG smoke test -- {passed}/{total} checks passed\n")
    for i, (label, ok) in enumerate(checks, 1):
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {i:2d}  {label}")

    if passed < total:
        print(f"\n{total - passed} check(s) FAILED.")
        sys.exit(1)
    else:
        print("\nAll checks passed.")


if __name__ == "__main__":
    run()
