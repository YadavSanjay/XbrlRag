"""XBRL hybrid query-time pipeline — orchestrates §2.2 of
``docs/design/XbrlRag_Architecture.md`` end-to-end.

This is the XBRL-canonical analogue of
:class:`~src.pipelines.sec10k.retrieval_pipeline.RetrievalPipeline`: a thin
orchestrator over the already-implemented stages in ``src/retriever/xbrl/``:

    user_query
       |
       v
    IntentRouter.route()            (entity_extractor + intent_router, §2.2.2)
       |
       v
    BranchDispatcher.decide()       (fact / narrative / plan_generator, §2.2.1)
       |
       +-- FACT_BRANCH_DIRECT  --> intent_result.plan          (1-step lookup)
       +-- NARRATIVE_BRANCH    --> intent_result.plan, or a default
       |                           {"steps": [{"op": "narrative", ...}]}
       +-- PLAN_GENERATOR      --> PlanGenerator.generate_plan()  (§2.2.3)
       |
       v
    PlanExecutor.execute(plan)      (§2.2 plan_executor; this module supplies
       |                             the `narrative_fn` -> XbrlNarrativeRetriever,
       |                             closing the "narrative branch reuses the
       |                             existing hybrid RAG path" gap)
       v
    XbrlRagSynthesizer.generate_answer()   (§2.2 synthesizer)
       |
       v
    XbrlFaithfulnessVerifier.verify()       (§2.2 faithfulness_verifier — cheap LLM
       |                                     pass, each statement vs its own cited
       |                                     facts/chunks; drops UNSUPPORTED statements)
       v
    XbrlRagResponse{answer_summary, answer_details[].{statement, fact_ids, chunk_ids}}

Usage::

    pipeline = XbrlRetrievalPipeline()
    response = pipeline.generate("What was NVIDIA's Data Center revenue in FY2026?")
    print(response.answer_summary)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from src.llm.gateway import LLMRefusalError
from src.llm.run_budget import RunHalt
from src.logging_config import get_logger
from src.observability.trace_context import (
    current_trace,
    log_event,
    trace_query,
    traced_stage,
)
from src.pipelines.xbrl.query_cache import CacheEpochReader, SemanticQueryCache
from src.retriever.xbrl.branch_dispatcher import BRANCH, BranchDispatcher, DispatchDecision
from src.retriever.xbrl.citation_integrity import evidence_index
from src.retriever.xbrl.entity_extractor import EntityExtractor, ResolvedCompany
from src.retriever.xbrl.intent_router import XBRL_INTENT, IntentRouter, IntentRouterResult
from src.retriever.xbrl.llm_xbrl_faithfulness_verifier import XbrlFaithfulnessVerifier
from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagResponse, XbrlRagSynthesizer
from src.retriever.xbrl.operation_cues import mark_missing_operation, operation_cue
from src.retriever.xbrl.plan_executor import (
    ExecutionResult,
    ExecutionStatus,
    PlanExecutor,
    StepResult,
    StepStatus,
)
from src.retriever.xbrl.plan_generator import NESTED_RANKING_ERROR, PlanGenerator
from src.retriever.xbrl.query_decomposer import TOP_TOKEN_RE, QueryDecomposer, SubQuery
from src.retriever.xbrl.ranked_top import ranked_top_text
from src.retriever.xbrl.screen_aggregate import append_aggregate_disclosures
from src.retriever.xbrl.screen_disclosure import append_screen_disclosures
from src.retriever.xbrl.xbrl_factstore_breakdown import XbrlFactstoreBreakdown
from src.retriever.xbrl.xbrl_factstore_lookup import XbrlFactstoreLookup
from src.retriever.xbrl.xbrl_factstore_screen import ScreenStatus, XbrlFactstoreScreen
from src.retriever.xbrl.xbrl_narrative_retriever import XbrlNarrativeRetriever
from src.retriever.xbrl.year_length_answers import append_year_length_disclosures

logger = get_logger(__name__)

# Fallback plan used for NARRATIVE_BRANCH when `intent_result.plan` is unset —
# e.g. the ambiguous-entity "degrade to narrative" path (branch_dispatcher §2.2.1
# step 3), where intent_router never produced a fast-path plan.
_DEFAULT_NARRATIVE_PLAN = {"steps": [{"id": "n1", "op": "narrative", "section_hint": None}]}


@dataclass(frozen=True)
class DependencyCompanies:
    """What an earlier sub-query offers a dependent ``<<TOP:id:n>>`` token.

    Attributes:
        screen_rows: The first `screen` step's rows in the step's own (rank) order, or
            None when the response has no screen step with rows.
        fallback_tickers: Distinct tickers named across the plan's lookup steps, in
            order; used when the screen yields no ticker (they are not a ranking).
    """

    screen_rows: list[dict] | None = None
    fallback_tickers: list[str] = field(default_factory=list)


class XbrlRetrievalPipeline:
    """End-to-end XBRL hybrid query pipeline.

    All sub-components are constructed from environment/config defaults
    (``XBRL_DB_*`` / ``OPENAI_API_KEY`` via each component's ``from_env`` or
    constructor default) unless explicitly injected — handy for tests.
    """

    def __init__(
        self,
        entity_extractor: EntityExtractor | None = None,
        intent_router: IntentRouter | None = None,
        query_decomposer: QueryDecomposer | None = None,
        plan_generator: PlanGenerator | None = None,
        factstore_lookup: XbrlFactstoreLookup | None = None,
        factstore_screen: XbrlFactstoreScreen | None = None,
        factstore_breakdown: XbrlFactstoreBreakdown | None = None,
        narrative_retriever: XbrlNarrativeRetriever | None = None,
        synthesizer: XbrlRagSynthesizer | None = None,
        faithfulness_verifier: XbrlFaithfulnessVerifier | None = None,
        known_tickers: list[str] | None = None,
        persist_audit: bool = True,
        query_cache: SemanticQueryCache | None = None,
    ):
        self.entity_extractor = entity_extractor or EntityExtractor.from_env()
        self.intent_router = intent_router or IntentRouter(
            entity_extractor=self.entity_extractor,
            known_tickers=known_tickers or [],
        )
        self.query_decomposer = query_decomposer or QueryDecomposer()
        self.plan_generator = plan_generator or PlanGenerator()
        self.factstore_lookup = factstore_lookup or XbrlFactstoreLookup.from_env()
        self.factstore_screen = factstore_screen or XbrlFactstoreScreen.from_env()
        self.factstore_breakdown = factstore_breakdown or XbrlFactstoreBreakdown.from_env()
        self.narrative_retriever = narrative_retriever or XbrlNarrativeRetriever()
        self.synthesizer = synthesizer or XbrlRagSynthesizer()
        self.faithfulness_verifier = faithfulness_verifier or XbrlFaithfulnessVerifier()

        self.dispatcher = BranchDispatcher(intent_router=self.intent_router)

        # WS-4 / B1: whole-query answer cache (disabled unless XBRLRAG_QUERY_CACHE set).
        self.query_cache = query_cache or SemanticQueryCache.from_env()
        self._cache_epoch = CacheEpochReader.from_env() if self.query_cache.enabled else None

        if persist_audit:
            self._enable_audit_persistence()

    @staticmethod
    def _enable_audit_persistence() -> None:
        """Register the Postgres audit sink so every query is persisted to the
        ``audit`` schema (Phase 3). Best-effort: if it can't be constructed, the
        pipeline still runs and telemetry remains in the ``xbrlrag.events`` stream."""
        from src.observability.audit_store import enable_audit_persistence

        enable_audit_persistence()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, query: str, user_id: str | None = None) -> XbrlRagResponse:
        """Decompose *query* if compound, then route/plan/execute/synthesize each
        sub-query and merge the answers.

        The whole call runs inside a per-query trace (:func:`trace_query`): every
        stage is timed, every LLM call records its tokens/cost, the routing
        decision is emitted as a structured ``decision`` event, and a
        ``query_summary`` is emitted on completion.

        A ``RunHalt`` from any stage is re-raised unchanged after the trace is
        annotated ``halted``; no partial answer is returned
        (docs/specs/xbrlrag-run-halt.md). Serving callers convert it at the API
        boundary (``src.api.pipelines.xbrl_answer``).
        """
        with trace_query(query, user_id=user_id):
            try:
                cached = self._cache_get(query)
                if cached is not None:
                    return cached
                response = self._generate_uncached(query)
                self._cache_put(query, response)
                return response
            except RunHalt as halt:
                self._annotate_halt(query, halt)
                raise
            except LLMRefusalError as refusal:
                return self._refused_response(query, refusal)

    def _generate_uncached(self, query: str) -> XbrlRagResponse:
        with traced_stage("decomposer"):
            subqueries = self.query_decomposer.decompose(query)
        if len(subqueries) <= 1:
            sub_q = subqueries[0].query if subqueries else query
            response = self._run_single(sub_q)
            retried = self._redecompose_on_refusal(query, response)
            return retried if retried is not None else response
        return self._run_decomposed(query, subqueries)

    # Plan error codes on which an undecomposed question is decomposed once more.
    _REDECOMPOSE_ON = frozenset({NESTED_RANKING_ERROR})

    def _redecompose_on_refusal(
        self, query: str, response: XbrlRagResponse
    ) -> XbrlRagResponse | None:
        """Decompose a question once more when its single plan was refused as compound.

        Called only for a question the first decomposition left whole; the planner's
        ``error_code`` (prompt v10+) says the question ranks within another ranking's
        result, which one plan cannot express. The decomposer is asked once, with that
        code as PLANNER_FEEDBACK, and a split result runs as a compound query. Bounded: the
        sub-queries run through :meth:`_run_single`, which never re-decomposes.

        Args:
            query: The user's original question.
            response: The answer of the single (undecomposed) run.

        Returns:
            The decomposed answer, or None to keep *response*: no retrying code, the retry
            did not split the question, or the retry failed.

        Raises:
            RunHalt: The run is stopping (budget, breaker); never swallowed.
        """
        code = (response.metadata or {}).get("plan_error_code")
        if code not in self._REDECOMPOSE_ON:
            return None
        log_event(
            "decision", point="redecompose_on_refusal", decision="retry", reason=code,
        )
        try:
            with traced_stage("decomposer"):  # accumulates with the first pass
                subqueries = self.query_decomposer.decompose(query, planner_feedback=code)
            if len(subqueries) <= 1:
                log_event(
                    "decision", point="redecompose_on_refusal", decision="not_split",
                    reason=code,
                )
                return None
            log_event(
                "decision", point="redecompose_on_refusal", decision="split", reason=code,
                subqueries=[sq.query for sq in subqueries],
            )
            return self._run_decomposed(query, subqueries)
        except RunHalt:
            raise  # the run is stopping, not the retry failing (docs/specs/xbrlrag-run-halt.md)
        except Exception as exc:  # isolation point: the retry never worsens the refusal
            logger.exception("xbrl_retrieval_pipeline: re-decomposition failed (query=%r)", query)
            log_event(
                "decision", point="redecompose_on_refusal", decision="failed", reason=code,
                error=type(exc).__name__,
            )
            self._annotate_outcome(response)  # a sub-query may have replaced the answer
            return None

    # ------------------------------------------------------------------
    # WS-4 / B1 — whole-query answer cache
    # ------------------------------------------------------------------

    def _cache_get(self, query: str) -> XbrlRagResponse | None:
        """Return a cached answer for *query* (after an epoch-triggered flush), or
        None. A hit is annotated on the trace and emitted as a `query_cache` event."""
        if not self.query_cache.enabled:
            return None
        if self._cache_epoch is not None:
            self.query_cache.maybe_invalidate(self._cache_epoch.current())
        cached = self.query_cache.get(query, self._cache_embed_fn)
        if cached is None:
            return None
        log_event("query_cache", outcome="hit", **self.query_cache.stats())
        trace = current_trace()
        if trace is not None:
            trace.annotate(cache_hit=True)
        return XbrlRagResponse.model_validate(cached)

    def _cache_put(self, query: str, response: XbrlRagResponse) -> None:
        if not self.query_cache.enabled or not self._is_cacheable(response):
            return
        self.query_cache.put(query, response.model_dump(), self._cache_embed_fn)

    @staticmethod
    def _is_cacheable(response: XbrlRagResponse) -> bool:
        """Cache real answers only — never a run halted by the cost ceiler/breaker,
        nor a hard execution error (those may succeed on a retry / next publish)."""
        if response.metadata.get("halted"):
            return False
        return response.execution_result.get("status") != "ERROR"

    def _cache_embed_fn(self, query: str) -> list[float]:
        """Dense query embedding for the L2 vector tier — reuses the pipeline's
        existing BGE-M3 / RagInference embedder so the model is not loaded twice.
        Only invoked when the L2 tier is enabled."""
        return self.narrative_retriever.embedding_generator.dense_embed_texts(
            [("q", query)]
        )["q"].values

    def generate_from_decision(self, query: str, decision: DispatchDecision) -> XbrlRagResponse:
        """Same as :meth:`generate`, given an already-computed
        :class:`DispatchDecision` (e.g. for logging/inspection of the routing
        decision before execution). Opens its own trace; skips decomposition."""
        with trace_query(query):
            try:
                self._log_decision(decision)
                return self._run(query, decision)
            except RunHalt as halt:
                self._annotate_halt(query, halt)
                raise
            except LLMRefusalError as refusal:
                return self._refused_response(query, refusal)

    # ------------------------------------------------------------------
    # Single-query path (one information need)
    # ------------------------------------------------------------------

    def _run_single(self, query: str) -> XbrlRagResponse:
        """Route one (already atomic) query and run it end-to-end. Assumes an
        active trace."""
        with traced_stage("intent_router"):
            decision = self.dispatcher.decide(query)
        self._log_decision(decision)
        return self._run(query, decision)

    # ------------------------------------------------------------------
    # Compound-query path (ordered, possibly dependent sub-queries)
    # ------------------------------------------------------------------

    def _run_decomposed(self, original_query: str, subqueries: list[SubQuery]) -> XbrlRagResponse:
        """Run each sub-query in dependency order, feeding an earlier screen's
        top-N companies into any dependent sub-query, then merge the answers."""
        responses: dict[str, XbrlRagResponse] = {}
        top_companies: dict[str, DependencyCompanies] = {}

        for sq in subqueries:
            resolved_query = self._inject_dependencies(sq, top_companies)
            log_event(
                "subquery", id=sq.id, depends_on=sq.depends_on,
                query=resolved_query, rewritten=(resolved_query != sq.query),
            )
            with traced_stage(f"subquery:{sq.id}"):
                resp = self._run_single(resolved_query)
            responses[sq.id] = resp
            top_companies[sq.id] = self._extract_top_companies(resp)

        merged = self._merge_responses(original_query, subqueries, responses)
        self._annotate_outcome(merged)
        return merged

    @staticmethod
    def _inject_dependencies(sq: SubQuery, top_companies: dict[str, DependencyCompanies]) -> str:
        """Replace each ``<<TOP:dep:n>>`` token in *sq* with dependency *dep*'s top *n*.

        A screen-backed dependency injects its rows with their rank
        (``ranked_top_text``: "AMZN (#1), MSFT (#2)", a bare ticker for ``n == 1``;
        docs/specs/ranked-top-injection.md). Otherwise the plan's lookup tickers are
        injected comma-separated without labels (they are not a ranking), else a
        readable placeholder.
        """
        def _sub(m) -> str:
            dep_id, n = m.group(1), int(m.group(2))
            dep = top_companies.get(dep_id)
            if dep is not None and dep.screen_rows is not None:
                ranked = ranked_top_text(dep.screen_rows, n)
                if ranked is not None:
                    return ranked
            tickers = (dep.fallback_tickers if dep is not None else [])[:n]
            if not tickers:
                logger.warning("subquery %s: no companies resolved for dependency %s", sq.id, dep_id)
                return "the top companies identified above"
            return ", ".join(tickers)

        return TOP_TOKEN_RE.sub(_sub, sq.query)

    @staticmethod
    def _extract_top_companies(response: XbrlRagResponse) -> DependencyCompanies:
        """What a dependent sub-query can reference from *response*.

        The first `screen` step with rows gives its rows in the step's own order (the
        screen numbers them rank 1, 2, ... in list order, so they are never re-sorted);
        the fallback is the distinct tickers named across the plan's lookup steps.
        """
        screen_rows: list[dict] | None = None
        for step in (response.execution_result or {}).get("steps", []):
            if step.get("op") == "screen" and step.get("rows"):
                screen_rows = list(step["rows"])
                break
        # Fallback: distinct tickers named across the plan's lookup steps, in order.
        seen: list[str] = []
        for step in (response.plan or {}).get("steps", []):
            t = step.get("ticker")
            if t and t not in seen:
                seen.append(t)
        return DependencyCompanies(screen_rows=screen_rows, fallback_tickers=seen)

    def _merge_responses(
        self, query: str, subqueries: list[SubQuery], responses: dict[str, XbrlRagResponse]
    ) -> XbrlRagResponse:
        """Concatenate the per-sub-query answers into one response. Each sub-query
        already ran its own faithfulness verifier, so nothing is re-verified.

        Step ids such as the fast path's ``f1`` repeat across sub-queries, so each merged
        ``answer_details`` entry keeps its ids unchanged and gains ``subquery_id``: its ids
        resolve against ``execution_result["subqueries"][subquery_id]``
        (docs/specs/citation-integrity.md, rule 2)."""
        summary_blocks: list[str] = []
        answer_details: list[dict] = []
        unanswered_parts: list[dict] = []
        narrative_chunks: list[dict] = []
        seen_chunk_ids: set = set()
        sub_meta: list[dict] = []
        fully = True

        for sq in subqueries:
            resp = responses.get(sq.id)
            if resp is None:
                continue
            if resp.answer_summary:
                summary_blocks.append(f"[{sq.id}] {resp.query}\n{resp.answer_summary}")
            answer_details.extend({**d, "subquery_id": sq.id} for d in resp.answer_details or [])
            unanswered_parts.extend(resp.unanswered_parts or [])
            for c in resp.narrative_chunks or []:
                cid = c.get("chunk_id")
                if cid in seen_chunk_ids:
                    continue
                seen_chunk_ids.add(cid)
                narrative_chunks.append(c)
            meta = resp.metadata or {}
            fully = fully and bool(meta.get("fully_answered", False))
            sub_meta.append({
                "id": sq.id, "query": resp.query, "depends_on": sq.depends_on,
                "fully_answered": meta.get("fully_answered"),
                "plan": resp.plan, "execution_result": resp.execution_result,
                "faithfulness": meta.get("faithfulness"),
            })

        return XbrlRagResponse(
            query=query,
            answer_summary="\n\n".join(summary_blocks),
            answer_details=answer_details,
            unanswered_parts=unanswered_parts,
            metadata={
                "fully_answered": fully,
                "decomposed": True,
                "subqueries": sub_meta,
            },
            plan={"decomposed": [sq.to_dict() for sq in subqueries]},
            execution_result={"subqueries": {sq.id: (responses[sq.id].execution_result
                                                     if sq.id in responses else None)
                                             for sq in subqueries}},
            narrative_chunks=narrative_chunks,
        )

    # ------------------------------------------------------------------
    # Internal: stage-timed execution (assumes an active trace)
    # ------------------------------------------------------------------

    def _run(self, query: str, decision: DispatchDecision) -> XbrlRagResponse:
        intent_result = decision.intent_result

        # A company mention that names no filer (or only resembles one) is never answered
        # for: an unfiltered narrative search would answer about whichever filer it hit
        # (docs/specs/entity-resolution-margin.md).
        unresolved = self._unresolved_company_mentions(intent_result)
        if unresolved:
            response = self._unresolved_company_response(query, unresolved, decision)
            self._annotate_outcome(response)
            return response

        with traced_stage("plan_generator"):
            plan = self._resolve_plan(query, decision, intent_result)

        if "error" in plan:
            error_response = self._error_response(query, plan, decision)
            self._annotate_outcome(error_response)
            return error_response

        with traced_stage("plan_exec"):
            execution_result = self._execute_plan(query, plan, intent_result)
        with traced_stage("synthesizer"):
            response = self.synthesizer.generate_answer(query, plan, execution_result)
        with traced_stage("verifier"):
            verified = self.faithfulness_verifier.verify(response)
        # State each screen's universe, exclusions and period ends; appended after the
        # verifier, which never judges it (docs/specs/screen-universe.md).
        verified.answer_summary = self._append_disclosures(
            verified.answer_summary or "", (verified.execution_result or {}).get("steps", [])
        )
        self._log_missing_year_length_notes(
            verified.answer_summary,
            (verified.execution_result or {}).get("steps", []),
            verified.answer_details or [],
        )
        verified = self._mark_missing_operation(query, verified)

        self._annotate_outcome(verified)
        return verified

    @staticmethod
    def _mark_missing_operation(query: str, response: XbrlRagResponse) -> XbrlRagResponse:
        """Declare an asked CAGR or ratio the answer does not state as unanswered.

        See docs/specs/plan-cagr-and-ratios.md, Requirement 9: the cue is read from this
        (sub-)query's own text; the components stay, so the answer is a hedge, never a
        confident wrong. Runs after the verifier and the disclosure sentences.

        Args:
            query: This (sub-)query's text.
            response: Its verified response.

        Returns:
            The response, rebuilt with the unanswered part when the asked value is missing.
        """
        operation = operation_cue(query)
        if operation is None:
            return response
        before = response.model_dump()
        marked = mark_missing_operation(before, query, operation)
        if marked == before:
            return response
        log_event(
            "decision", point="operation_answer", decision="asked_value_missing",
            operation=operation, fully_answered=False,
        )
        return XbrlRagResponse.model_validate(marked)

    @staticmethod
    def _append_disclosures(summary: str, steps: list[dict]) -> str:
        """Append the fixed disclosure sentences to an answer, after the verifier.

        The screen sentences first (universe, named exclusions, period ends: S2, S3;
        docs/specs/screen-universe.md), then each growth screen's members whose compared
        years differ in length (docs/specs/period-year-length-answers.md), then the
        aggregate sentences (the aggregated members; docs/specs/screen-aggregates.md R9).

        Args:
            summary: The verified answer summary.
            steps: The query's step results as ``StepResult.to_dict()`` dicts.

        Returns:
            The summary with every new sentence appended.
        """
        summary = append_screen_disclosures(summary, steps)
        summary = append_year_length_disclosures(summary, steps)
        return append_aggregate_disclosures(summary, steps)

    @staticmethod
    def _log_missing_year_length_notes(
        summary: str, steps: list[dict], details: list[dict]
    ) -> list[dict]:
        """Log a decision event for each compute or cagr year-length note the answer omits.

        The synthesizer states an OK compute's or cagr's ``year_lengths`` note verbatim
        (docs/specs/period-year-length-answers.md, review point 1); an omitted or
        paraphrased note would otherwise be silent. A note counts as stated when it appears
        verbatim in the summary or in any answer statement citing its step: a ``fact_ids``
        entry that resolves to that step (``citation_integrity.evidence_index``).

        Args:
            summary: The final answer summary.
            steps: The query's step results as ``StepResult.to_dict()`` dicts.
            details: The final ``answer_details`` (``statement``, ``fact_ids``, ...).

        Returns:
            One ``{"step_id", "fact_ids"}`` per note found verbatim in neither ``summary``
            nor a statement citing its step.
        """
        index = evidence_index({"steps": steps})
        missing: list[dict] = []
        for step in steps:
            entries = step.get("year_lengths")
            if step.get("op") not in ("compute", "cagr") or step.get("status") != "OK":
                continue
            if not isinstance(entries, list):
                continue
            texts = [summary] + [
                str(d.get("statement") or "")
                for d in details
                if isinstance(d, dict)
                and any(
                    any(r is step for r in index.get(str(f), []))
                    for f in d.get("fact_ids") or []
                )
            ]
            for entry in entries:
                note = entry.get("note") if isinstance(entry, dict) else None
                if isinstance(note, str) and not any(note in t for t in texts):
                    missing.append(
                        {"step_id": step.get("step_id"), "fact_ids": entry.get("fact_ids")}
                    )
        for item in missing:
            log_event(
                "decision", point="year_length_note", decision="note_missing_from_answer",
                step_id=item["step_id"], fact_ids=item["fact_ids"],
            )
        return missing

    @staticmethod
    def _log_decision(decision: DispatchDecision) -> None:
        """Emit the routing decision as a structured event and annotate the trace."""
        intent_result = decision.intent_result
        log_event(
            "decision",
            point="branch_dispatch",
            branch=decision.branch.value,
            intent=intent_result.intent.value,
            degraded=decision.degraded,
            fast_path=intent_result.plan is not None,
            reason=decision.reason,
        )
        trace = current_trace()
        if trace is not None:
            trace.annotate(
                branch=decision.branch.value,
                intent=intent_result.intent.value,
                degraded=decision.degraded,
            )

    @staticmethod
    def _annotate_outcome(response: XbrlRagResponse) -> None:
        trace = current_trace()
        if trace is None:
            return
        metadata = getattr(response, "metadata", None)
        if isinstance(metadata, dict):
            trace.annotate(fully_answered=metadata.get("fully_answered"))
        # Stash the full answer so the Trace Explorer can render the same view
        # the Ask page shows (persisted to audit.query_audit.answer).
        try:
            trace.answer = response.model_dump()
        except Exception:  # never let telemetry break the request path
            logger.exception("could not capture answer on trace for query_id=%s", trace.query_id)

    # ------------------------------------------------------------------
    # Plan resolution per branch (§2.2.1)
    # ------------------------------------------------------------------

    def _resolve_plan(self, query: str, decision: DispatchDecision, intent_result: IntentRouterResult) -> dict:
        if decision.branch == BRANCH.FACT_BRANCH_DIRECT:
            # intent_router fast path always sets `plan` for this branch (see
            # branch_dispatcher.decide_from_result step 1).
            return intent_result.plan

        if decision.branch == BRANCH.NARRATIVE_BRANCH:
            return intent_result.plan or _DEFAULT_NARRATIVE_PLAN

        # BRANCH.PLAN_GENERATOR — NUMERIC_COMPUTE / HYBRID / dimensional NUMERIC_LOOKUP
        return self.plan_generator.generate_plan(query, intent_result)

    # ------------------------------------------------------------------
    # Execution (§2.2 plan_executor, with narrative_fn wired to XbrlNarrativeRetriever)
    # ------------------------------------------------------------------

    def _execute_plan(self, query: str, plan: dict, intent_result: IntentRouterResult) -> ExecutionResult:
        executor = PlanExecutor(
            factstore_lookup=self.factstore_lookup,
            narrative_fn=lambda step: self.narrative_retriever.execute_step(step, query, intent_result),
            screen_fn=self._screen_step,
            breakdown_fn=self._breakdown_step,
        )
        return executor.execute(plan)

    # Cross-filer screen status -> step status (mirrors _LOOKUP_TO_STEP_STATUS).
    _SCREEN_TO_STEP_STATUS = {
        ScreenStatus.OK: StepStatus.OK,
        ScreenStatus.NOT_FOUND: StepStatus.NOT_FOUND,
        ScreenStatus.ERROR: StepStatus.ERROR,
    }

    def _screen_step(self, step: dict) -> StepResult:
        """`screen_fn` for the executor: run the cross-filer screen and wrap its
        ranked `rows` in a `StepResult` (no single scalar value)."""
        screen_result = self.factstore_screen.screen(step)
        return StepResult(
            step_id=screen_result.step_id,
            op="screen",
            status=self._SCREEN_TO_STEP_STATUS.get(screen_result.status, StepStatus.ERROR),
            rows=screen_result.rows,
            excluded=list(screen_result.excluded),
            disclosure=screen_result.disclosure,
            error=screen_result.error,
            detail=screen_result,
            # An aggregate over trimmed rows would be silently partial
            # (docs/specs/screen-aggregates.md, Requirement 7).
            row_cap=screen_result.row_cap,
            # Ranked members whose compared years differ in length; the pipeline appends
            # their sentence (docs/specs/period-year-length-answers.md).
            year_lengths=list(screen_result.year_lengths),
        )

    def _breakdown_step(self, step: dict) -> StepResult:
        """`breakdown_fn` for the executor: one filer's partition by axis, as rows
        (docs/specs/breakdown-op.md). A reconciling row (`kind` "reconciling") and the
        disclosure (total, reconciling line, set-aside splits, gap) go to the synthesizer
        (docs/specs/breakdown-reconciling-items.md)."""
        result = self.factstore_breakdown.breakdown(step)
        return StepResult(
            step_id=result.step_id,
            op="breakdown",
            status=self._SCREEN_TO_STEP_STATUS.get(result.status, StepStatus.ERROR),
            rows=result.rows,
            disclosure=result.disclosure,
            error=result.error or ("; ".join(result.resolution_notes) if not result.is_ok else None),
            detail=result,
        )

    # ------------------------------------------------------------------
    # Error path (plan_generator returned {"error": ...})
    # ------------------------------------------------------------------

    @staticmethod
    def _annotate_halt(query: str, halt: RunHalt) -> None:
        """Marks the active trace as halted before the halt leaves the pipeline.

        A run halt (budget, breaker, Flex cap, stop) is the run deciding to stop, so the
        pipeline re-raises it to its caller (docs/specs/xbrlrag-run-halt.md). The eval
        runner ends the run aborted and resumable; a serving caller gets the halted
        "not answered" body from ``src.api.pipelines.xbrl_answer``.

        Args:
            query: The user's query.
            halt: The halt a stage raised.
        """
        logger.warning(
            "xbrl_retrieval_pipeline: run halted (%s): %s (query=%r)",
            type(halt).__name__, halt, query,
        )
        trace = current_trace()
        if trace is not None:
            trace.annotate(halted=True, halt_reason=type(halt).__name__)

    @staticmethod
    def _refused_response(query: str, refusal: LLMRefusalError) -> XbrlRagResponse:
        """A declined answer when a stage's model refused (llm-anthropic-and-caching).

        A refusal is final: the gateway never retries it, no stage escalates on it, and
        the query returns a clean 'not answered' (never cached) rather than an exception.

        Args:
            query: The user's query.
            refusal: The refusal raised by a stage's LLM call.

        Returns:
            A not-fully-answered response flagged ``refused``.
        """
        reason = f"the model declined to answer: {refusal}"
        logger.warning("xbrl_retrieval_pipeline: %s (query=%r)", reason, query)
        trace = current_trace()
        if trace is not None:
            trace.annotate(refused=True)
        execution_result = ExecutionResult(status=ExecutionStatus.ERROR, error=reason)
        return XbrlRagResponse(
            query=query,
            answer_summary="",
            answer_details=[],
            unanswered_parts=[{"question_fragment": query, "reason": reason}],
            metadata={
                "fully_answered": False,
                "notes": "a model declined to answer (provider refusal)",
                "refused": True,
            },
            plan={},
            execution_result=execution_result.to_dict(),
            narrative_chunks=[],
        )

    @staticmethod
    def _unresolved_company_mentions(intent_result: IntentRouterResult) -> list[ResolvedCompany]:
        """The extracted company mentions that resolved to no filer.

        A cross-filer screen is exempt: it ranks the whole corpus and never reads a
        company mention as its subject.

        Args:
            intent_result: The router's output.

        Returns:
            The ambiguous or unresolved companies (empty when every mention resolved).
        """
        if intent_result.intent == XBRL_INTENT.NUMERIC_SCREEN:
            return []
        return [c for c in intent_result.companies if c.is_ambiguous and c.mention.strip()]

    @staticmethod
    def _unresolved_company_response(
        query: str, companies: list[ResolvedCompany], decision: DispatchDecision
    ) -> XbrlRagResponse:
        """A not-answered response that names the closest filers for each mention.

        Args:
            query: The user's query.
            companies: The mentions that did not resolve.
            decision: The routing decision (recorded in the metadata).

        Returns:
            A not-fully-answered response flagged ``unresolved_company``, never cached.
        """
        reason = "; ".join(c.clarification() for c in companies)
        logger.warning("xbrl_retrieval_pipeline: unresolved company for query=%r: %s", query, reason)
        log_event(
            "decision",
            point="unresolved_company",
            mentions=[c.mention for c in companies],
            candidates=[c.candidates for c in companies],
        )
        trace = current_trace()
        if trace is not None:
            trace.annotate(unresolved_company=True)
        execution_result = ExecutionResult(status=ExecutionStatus.ERROR, error=reason)
        return XbrlRagResponse(
            query=query,
            answer_summary="",
            answer_details=[],
            unanswered_parts=[{"question_fragment": query, "reason": reason}],
            metadata={
                "fully_answered": False,
                "notes": "a company mention did not resolve to a filer in the corpus",
                "unresolved_company": True,
                "candidates": {c.mention: c.candidates for c in companies},
                "branch": decision.branch.value,
                "degraded": decision.degraded,
                "dispatch_reason": decision.reason,
            },
            plan={},
            execution_result=execution_result.to_dict(),
            narrative_chunks=[],
        )

    @staticmethod
    def _error_response(query: str, plan: dict, decision: DispatchDecision) -> XbrlRagResponse:
        err = plan.get("error", "plan generation failed")
        execution_result = ExecutionResult(status=ExecutionStatus.ERROR, error=str(err))
        logger.warning("xbrl_retrieval_pipeline: plan error for query=%r: %s", query, err)
        return XbrlRagResponse(
            query=query,
            answer_summary="",
            answer_details=[],
            unanswered_parts=[{"question_fragment": query, "reason": str(err)}],
            metadata={
                "fully_answered": False,
                "notes": "plan generation failed",
                "branch": decision.branch.value,
                "degraded": decision.degraded,
                "dispatch_reason": decision.reason,
                # The planner's error kind (prompt v10+); a nested ranking is
                # re-decomposed once (_redecompose_on_refusal).
                **({"plan_error_code": str(plan["error_code"])}
                   if plan.get("error_code") else {}),
            },
            plan=plan,
            execution_result=execution_result.to_dict(),
            narrative_chunks=[],
        )


if __name__ == "__main__":
    pipeline = XbrlRetrievalPipeline()

    for q in [
        "What was NVIDIA's Data Center revenue in FY2026?",
        "What drove the growth in NVIDIA's Data Center revenue in FY2026?",
        "What are the main risk factors NVIDIA discusses related to export controls?",
    ]:
        response = pipeline.generate(q)
        print(q)
        print(f"  answer_summary: {response.answer_summary}")
        for detail in response.answer_details:
            print(f"  detail: {detail}")
        print()
