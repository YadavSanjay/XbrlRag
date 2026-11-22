"""The html_rag arm: a competent RAG over the filings' HTML, with no XBRL in it.

The baseline XbrlRag has to beat. It resolves the question with XbrlRag's own router, as the
long-context arms do, and reads only ``companies`` and ``periods``; the case's answer fields are
never read. It searches ``html10k_text`` on what resolved (:func:`search_scopes`), one hybrid search
and one rerank per filer, expands with neighbours and table parts, and makes one answer call on
stage ``html_rag``. The excerpts are JSON in the user message. The reply cites table cells and
passages, and the calculator produces every number (:mod:`src.retriever.html10k.answerer`).

When the resolver can't choose, the arm still searches, on what did resolve, and records why in
``selection["degraded"]``: XbrlRag degrades the same way, so refusing here would flatter it. There
are no exclusions, so every case stays in the denominator.

At query time this reads the ``entity_master`` and ``fiscal_calendar`` tables, through the
resolver, and the Qdrant collection ``html10k_text``. It reads no filing store and no fact.

Spec: [docs/specs/html-rag-arm.md](../../../docs/specs/html-rag-arm.md).
"""

from __future__ import annotations

import json
import threading
from typing import Any

from src.eval.core.answer_parsing import _first_object
from src.eval.core.case import EvalCase
from src.eval.core.system import SystemAnswer
from src.eval.systems._stage_llm import StageLLM, answer_in_trace
from src.llm.gateway import LLMGateway
from src.llm.stage_models import resolve_stage
from src.retriever.html10k.answerer import ReplyOutcome, build_excerpts, resolve_reply
from src.retriever.html10k.config import Html10kRagConfig, load_html10k_config
from src.retriever.html10k.rerank_cache import CachedReranker
from src.retriever.html10k.retriever import Html10kRetrieval, Html10kRetriever, RetrievedChunk
from src.retriever.html10k.scopes import search_scopes
from src.retriever.xbrl.entity_extractor import EntityExtractor
from src.retriever.xbrl.intent_router import IntentRouter

STAGE = "html_rag"
# The stage whose model this arm uses, so a comparison with XbrlRag isn't a comparison of models.
MODEL_STAGE = "synthesizer"
_NO_CANDIDATES = "no chunk was found for this question"


def _retrieved_record(chunk: RetrievedChunk) -> dict[str, Any]:
    """One chunk in ``selection``; with its byte range a run can be scored afterwards."""
    return {
        "chunk_id": chunk.chunk_id,
        "accession_no": chunk.accession_no,
        "ticker": chunk.ticker,
        "fiscal_year": chunk.fiscal_year,
        "section_key": chunk.section_key,
        "kind": "table" if chunk.contains_table else "prose",
        "html_char_start": chunk.html_char_start,
        "html_char_end": chunk.html_char_end,
        "rerank_score": chunk.rerank_score,
        "is_expansion": chunk.is_expansion,
    }


class HtmlRagSystem:
    """Resolves the question, retrieves from ``html10k_text`` and answers with citations.

    Thread-safe: the backends are built once under a lock, and the score cache runs one rerank at
    a time.
    """

    name = "html_rag"

    def __init__(
        self,
        *,
        gateway: LLMGateway | None = None,
        router: IntentRouter | None = None,
        embedder: Any = None,
        qdrant_client: Any = None,
        reranker: Any = None,
        config: Html10kRagConfig | None = None,
    ) -> None:
        """Build the arm; touches no network, database or store.

        Args:
            gateway: The gateway for the answer call; built on first use when omitted.
            router: XbrlRag's intent router; built on first use when omitted.
            embedder: The query embedder; RagInference on first use when omitted.
            qdrant_client: The Qdrant client; built on first use when omitted.
            reranker: The reranker, which the arm wraps in the score cache; RagInference on first
                use when omitted.
            config: The settings; the committed file when omitted.
        """
        self.model = resolve_stage(MODEL_STAGE).model
        self._llm = StageLLM(STAGE, gateway, model=self.model)
        self._config = config if config is not None else load_html10k_config()
        self._router = router
        self._embedder = embedder
        self._qdrant_client = qdrant_client
        self._reranker = reranker
        self._retriever: Html10kRetriever | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lazily built backends (double-checked locking: answer() runs concurrently).
    # ------------------------------------------------------------------

    def _get_router(self) -> IntentRouter:
        if self._router is None:
            with self._lock:
                if self._router is None:
                    # As long_context_arms builds it.
                    self._router = IntentRouter(
                        entity_extractor=EntityExtractor.from_env(), known_tickers=[]
                    )
        return self._router

    def _build_retriever(self) -> Html10kRetriever:
        config = self._config
        embedder = self._embedder
        if embedder is None:
            from src.embeddings.remote_embedder import RemoteEmbedder

            embedder = RemoteEmbedder()
        client = self._qdrant_client
        if client is None:
            from src.vectorstores.qdrant_vectordb_client import QdrantVectorDBClient

            client = QdrantVectorDBClient().client
        reranker = self._reranker
        if reranker is None:
            from src.retriever.remote_reranker import RemoteReranker

            reranker = RemoteReranker(model=config.reranker_model)
        return Html10kRetriever(
            embedder=embedder,
            client=client,
            reranker=CachedReranker(
                reranker, model=config.reranker_model, cache_path=config.rerank_cache_path
            ),
            config=config,
        )

    def _get_retriever(self) -> Html10kRetriever:
        if self._retriever is None:
            with self._lock:
                if self._retriever is None:
                    self._retriever = self._build_retriever()
        return self._retriever

    # ------------------------------------------------------------------

    def answer(self, case: EvalCase) -> SystemAnswer:
        """Answer one case.

        Args:
            case: The eval case. Only its question is read.

        Returns:
            The answer or a refusal, with its ``selection``, trace id, cost and latency.

        Raises:
            RunHalt: The run's budget or circuit breaker stopped a call.
        """
        record: dict[str, Any] = {}
        # answer_in_trace replaces the answer on an error or a provider refusal, so the record is
        # filled in as the arm goes and stamped here.
        answer = answer_in_trace(case.question, lambda: self._produce(case.question, record))
        if record:
            if record["status"] is None:
                record["status"] = "error" if answer.error else "model_declined"
                record["reason"] = answer.error or answer.text
            answer.selection = record
        return answer

    def _produce(self, question: str, record: dict[str, Any]) -> SystemAnswer:
        result = self._get_router().route(question)
        scopes, degraded = search_scopes(result, self._config.max_filer_searches)
        record.update(
            status=None,
            reason=None,
            degraded=degraded,
            scopes=scopes,
            retrieved=[],
            rerank_calls=0,
            rerank_cache_hits=0,
            rerank_ms=0.0,
            calculations=[],
            unverified=[],
            retrieval_config=self._config.model_dump(exclude={"rerank_cache_path"}),
        )

        retrieval = self._get_retriever().retrieve(question, scopes)
        self._record_retrieval(record, retrieval)
        if not retrieval.chunks:
            record.update(status="no_candidates", reason=_NO_CANDIDATES)
            return SystemAnswer(text=_NO_CANDIDATES, refused=True)

        excerpts = json.dumps(build_excerpts(retrieval.chunks), ensure_ascii=False)
        reply = self._llm.complete(
            self._llm.prompt.user.format(question=question, excerpts=excerpts)
        )
        outcome = resolve_reply(_first_object(reply), retrieval.chunks)
        record.update(
            status=outcome.status,
            reason=outcome.reason,
            calculations=outcome.calculations,
            unverified=outcome.unverified,
        )
        return _to_answer(outcome)

    @staticmethod
    def _record_retrieval(record: dict[str, Any], retrieval: Html10kRetrieval) -> None:
        record.update(
            retrieved=[_retrieved_record(chunk) for chunk in retrieval.chunks],
            rerank_calls=retrieval.rerank_calls,
            rerank_cache_hits=retrieval.rerank_cache_hits,
            rerank_ms=retrieval.rerank_ms,
        )


def _to_answer(outcome: ReplyOutcome) -> SystemAnswer:
    """The graded answer; ``fact_ids`` stays empty, since nothing here comes from a fact."""
    return SystemAnswer(
        text=outcome.text,
        numeric_values=outcome.numeric_values,
        rows=outcome.rows,
        fact_ids=[],
        chunk_ids=outcome.chunk_ids,
        refused=outcome.refused,
        hedged=outcome.hedged,
    )
