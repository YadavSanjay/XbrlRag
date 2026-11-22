"""The naive-RAG baseline arm: top-10 hybrid search, then one LLM call.

What a reader would build first: embed the question with the same BGE-M3 dense+sparse
embedder XbrlRag uses, run one RRF-fused hybrid query against the same Qdrant
collection (``xbrl_sec10k_text``, ``limit=10``), and hand every retrieved chunk to one
LLM call (stage ``naive_rag``, on the synthesizer's tier). There is no plan, no fact
store, no reranker (about 2.8 s per chunk on the CPU this runs on) and no neighbour
expansion.

Two variants:

- ``naive_rag_plain``: no filters.
- ``naive_rag_filtered``: restricted to the case's ``ticker`` and ``fy``, each only when
  the case has it, so a cross-filer screen is filtered by fiscal year only.

Chunk text is untrusted filing text: it reaches the model only inside the user
message, serialised with ``json.dumps``. Citations are the reply's ``cited_chunks``
restricted to the chunks actually retrieved for the case.

Spec: [docs/specs/baseline-arms.md](../../../docs/specs/baseline-arms.md).
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from src import XBRL_TEXT_COLLECTION_NAME
from src.embeddings.base import BaseEmbeddingGenerator
from src.eval.core.answer_parsing import parse_structured_answer
from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer
from src.eval.systems._stage_llm import StageLLM, answer_in_trace
from src.llm.gateway import LLMGateway
from src.logging_config import get_logger

logger = get_logger(__name__)

STAGE = "naive_rag"
TOP_K = 10

# Source fields copied from each point's payload next to its text, so the model can
# tell which filer, year and section an excerpt comes from.
_SOURCE_FIELDS = ("ticker", "company_name", "fiscal_year", "form_type", "section_path")


def _build_default_embedder() -> BaseEmbeddingGenerator:
    """The shared RagInference embedder when ``RAGINFER_URL`` is set, else local BGE-M3.

    Mirrors ``XbrlNarrativeRetriever._build_embedding_generator``; both imports are
    deferred so the torch-free path never loads the local model's dependencies.
    """
    if os.getenv("RAGINFER_URL", "").strip():
        from src.embeddings.remote_embedder import RemoteEmbedder

        return RemoteEmbedder()
    from src.embeddings.xbrl_bge_embedding import XBRLBGEEmbeddingGenerator

    return XBRLBGEEmbeddingGenerator()


def _build_default_vector_db_client() -> Any:
    """The XBRL Qdrant client the narrative retriever uses (endpoint from ``QDRANT_*``)."""
    from src.vectorstores.xbrl_qdrant_vectordb_client import XBRLQdrantVectorDBClient

    return XBRLQdrantVectorDBClient()


def _cited_retrieved(cited: list[str], retrieved: list[str]) -> list[str]:
    """The cited ids that were retrieved, in order of first citation, deduplicated."""
    allowed = set(retrieved)
    out: list[str] = []
    for chunk_id in cited:
        if chunk_id in allowed and chunk_id not in out:
            out.append(chunk_id)
    return out


class NaiveRagSystem:
    """Retrieves the top chunks for a case and answers from them in one LLM call."""

    name: str

    def __init__(
        self,
        *,
        filtered: bool = False,
        gateway: LLMGateway | None = None,
        embedder: BaseEmbeddingGenerator | None = None,
        vector_db_client: Any = None,
    ) -> None:
        """Builds the arm.

        Args:
            filtered: Restrict retrieval to the case's ticker and fiscal year.
            gateway: The gateway for the ``naive_rag`` stage; built on first use when
                omitted.
            embedder: The query embedder; built on first use when omitted.
            vector_db_client: The Qdrant client; built on first use when omitted.
        """
        self.filtered = filtered
        self.name = "naive_rag_filtered" if filtered else "naive_rag_plain"
        self._llm = StageLLM(STAGE, gateway)
        self._embedder = embedder
        self._vector_db_client = vector_db_client
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lazily built retrieval backends (double-checked locking: answer() runs
    # concurrently and the embedder may load a multi-GB model).
    # ------------------------------------------------------------------

    def _get_embedder(self) -> BaseEmbeddingGenerator:
        if self._embedder is None:
            with self._lock:
                if self._embedder is None:
                    self._embedder = _build_default_embedder()
        return self._embedder

    def _get_vector_db_client(self) -> Any:
        if self._vector_db_client is None:
            with self._lock:
                if self._vector_db_client is None:
                    self._vector_db_client = _build_default_vector_db_client()
        return self._vector_db_client

    # ------------------------------------------------------------------

    def answer(self, case: EvalCase) -> SystemAnswer:
        """Answers one case from the chunks retrieved for its question.

        Args:
            case: The eval case.

        Returns:
            The parsed answer, citing only retrieved chunks, with its trace id, cost and
            latency.

        Raises:
            RunHalt: The run's budget or circuit breaker stopped the call.
        """
        return answer_in_trace(case.question, lambda: self._answer(case))

    def _retrieve(self, case: EvalCase) -> list[Any]:
        dense, sparse = self._get_embedder().embed_texts([("query", case.question)])["query"]
        tickers = [case.ticker] if self.filtered and case.ticker is not None else None
        years = [case.fy] if self.filtered and case.fy is not None else None
        result = self._get_vector_db_client().hybrid_query_text(
            collection_name=XBRL_TEXT_COLLECTION_NAME,
            vector_d=dense,
            vector_s=sparse,
            ciks_in_query=None,
            fiscal_years_in_query=years,
            tickers_in_query=tickers,
            limit=TOP_K,
        )
        return list(result.points)

    def _answer(self, case: EvalCase) -> SystemAnswer:
        points = self._retrieve(case)
        if not points:
            logger.info("naive_rag: no chunks retrieved for case %s; refusing", case.id)
            return SystemAnswer(text="", refused=True)

        excerpts = []
        for point in points:
            payload = point.payload or {}
            excerpt: dict[str, Any] = {"chunk_id": str(point.id)}
            excerpt.update({k: payload.get(k) for k in _SOURCE_FIELDS if k in payload})
            excerpt["text"] = payload.get("text", "") or ""
            excerpts.append(excerpt)
        user_text = self._llm.prompt.user.format(
            question=case.question,
            excerpts=json.dumps(excerpts, indent=2, ensure_ascii=False, default=str),
        )

        answer = parse_structured_answer(self._llm.complete(user_text))
        answer.chunk_ids = _cited_retrieved(
            answer.chunk_ids, [e["chunk_id"] for e in excerpts]
        )
        answer.fact_ids = []
        return answer
