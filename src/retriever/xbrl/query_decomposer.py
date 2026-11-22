"""query_decomposer — the pre-routing stage that splits a COMPOUND query into an
ordered list of self-contained sub-queries (design doc §2.2, "decomposition").

Most queries are a single information need and pass straight through as one
sub-query. But a bundled question like

    "Which companies have significant China revenue exposure AND for the top 3
     such companies what are the main supply-chain risk factors…?"

mixes a cross-filer numeric *screen* (part A) with a *narrative* ask scoped to
part A's result (part B) — two different retrieval strategies with a dependency.
A single intent-router call cannot serve both, so this stage (one cheap gpt-5-mini
call) emits ordered sub-queries; :class:`~src.pipelines.xbrl.retrieval_pipeline.
XbrlRetrievalPipeline` runs them in dependency order, substitutes an earlier
screen's top-N companies into a dependent sub-query (the ``<<TOP:id:n>>`` token),
and merges the answers.

Fail-safe: any LLM/parse error (or a non-compound query) yields a single
passthrough sub-query equal to the original query, so decomposition can never
make a query worse than the pre-decomposition behaviour.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional

from prompts.prompt_registry import PromptRegistry, get_xbrl_registry
from src.llm.gateway import extract_json, resolve_model_ref, stage_call_params
from src.llm.run_budget import RunHalt
from src.llm.stage_models import require_api_key, resolve_stage
from src.llm.xbrl_gateway import build_xbrl_gateway
from src.logging_config import get_logger
from src.observability.trace_context import log_event
from src.retriever.xbrl.operation_cues import operation_kinds, split_loses_operation

logger = get_logger(__name__)

# `<<TOP:sq1:3>>` -> ("sq1", 3): a dependent sub-query's reference to the top-N
# companies of an earlier sub-query's screen result.
TOP_TOKEN_RE = re.compile(r"<<TOP:([A-Za-z0-9_]+):(\d+)>>")


@dataclass
class SubQuery:
    id: str
    query: str
    depends_on: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "query": self.query, "depends_on": self.depends_on}

    @property
    def top_refs(self) -> list[tuple]:
        """(dep_id, n) pairs referenced by ``<<TOP:dep:n>>`` tokens in this query."""
        return [(m.group(1), int(m.group(2))) for m in TOP_TOKEN_RE.finditer(self.query)]


class QueryDecomposer:
    """Splits a compound query into ordered, self-contained sub-queries."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        max_subqueries: int = 5,
        prompt_version: str | None = None,
        prompt_provider: str | None = None,
        prompt_series: str | None = None,
        prompt_registry: PromptRegistry | None = None,
    ):
        # The model comes from the LLM profile active now (an explicit model wins), and
        # the key from that model's provider (docs/specs/llm-provider-profiles.md).
        stage_model = resolve_stage("query_decomposer", model)
        self.api_key = require_api_key(stage_model.model, stage_model.settings, api_key)
        self.model = stage_model.model
        self._llm_settings = stage_model.settings
        self.max_subqueries = max_subqueries

        self._model_ref = resolve_model_ref(self.model)
        self.model_config = self._model_ref.config
        self._registry = prompt_registry or get_xbrl_registry()
        self._prompt = self._registry.resolve(
            "query_decomposer",
            provider=prompt_provider or self._model_ref.provider,
            series=prompt_series or self._model_ref.series,
            version=prompt_version,
        )
        # System prompt is static and contains literal JSON braces + <<TOP:..>>
        # tokens — use it raw, never .format() it.
        self._system_prompt_text = self._prompt.system.template
        self._prompt_sig = self._prompt.signature()
        self._llm_client = build_xbrl_gateway("decomposer", api_key=api_key)

        logger.info(f"Initialized XBRL Query Decomposer with model={self.model}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def decompose(
        self, query: str, *, planner_feedback: str | None = None, **kwargs
    ) -> list[SubQuery]:
        """Return 1+ ordered sub-queries. A single-element list means "run as-is".

        Args:
            query: The user's question.
            planner_feedback: Why the planner could not answer the question as one need
                (a plan ``error_code`` such as ``"nested_ranking"``), on the one
                re-decomposition after that refusal; None on the first pass. It reaches
                the prompt as the PLANNER_FEEDBACK value (prompt v4+).
            **kwargs: Passed to the gateway call.

        Returns:
            The ordered sub-queries; the question itself as one sub-query on any error.

        Raises:
            RunHalt: The run is stopping (budget, breaker); never swallowed.
        """
        passthrough = [SubQuery(id="sq1", query=query, depends_on=[])]
        try:
            raw = self._do_llm_call(self._build_messages(query, planner_feedback), **kwargs)
        except RunHalt:
            raise  # the run is stopping, not the call failing (docs/specs/xbrlrag-run-halt.md)
        except Exception as e:
            logger.error(f"query decomposer LLM call failed: {e}; passing query through as-is")
            return passthrough

        subqueries = self._parse(raw, query)
        subqueries = self._order_by_dependency(subqueries)
        # One operation's operands are never separated: a ratio split into its two
        # components cannot divide (docs/specs/plan-cagr-and-ratios.md, Requirement 8).
        if split_loses_operation(query, [sq.query for sq in subqueries]):
            log_event(
                "decision", point="query_decomposer", decision="keep_operation_whole",
                operations=sorted(operation_kinds(query)),
                subqueries=[sq.query for sq in subqueries],
            )
            logger.info(
                "query_decomposer: split separated an operation's operands; passing through"
            )
            return passthrough
        # A dependent sub-query the model wrote without the question's one fiscal year
        # inherits it (docs/specs/dependent-subquery-period.md). Imported here: the module
        # imports SubQuery / TOP_TOKEN_RE from this one. A dependent sub-query's lone
        # "from <year>" is rewritten to a base-to-end range instead
        # (docs/specs/subquery-period-phrases.md).
        from src.retriever.xbrl.subquery_period import (
            from_base_year,
            from_end_year,
            inherit_dependent_periods,
            stated_fiscal_year,
        )

        inherited = inherit_dependent_periods(query, subqueries)
        for before, after in zip(subqueries, inherited, strict=True):
            if after.query == before.query:
                continue
            base_year = from_base_year(before.query)
            if base_year is not None:
                log_event(
                    "decision", point="query_decomposer", decision="from_end_year",
                    subquery=after.id, base_year=base_year,
                    end_year=from_end_year(query, base_year),
                )
            else:
                log_event(
                    "decision", point="query_decomposer", decision="inherit_period",
                    subquery=after.id, fiscal_year=stated_fiscal_year(query),
                )
        subqueries = inherited
        logger.info(
            "query_decomposer: query=%r -> %d sub-quer%s: %s",
            query, len(subqueries), "y" if len(subqueries) == 1 else "ies",
            [sq.id for sq in subqueries],
        )
        return subqueries

    # ------------------------------------------------------------------
    # Parsing / ordering
    # ------------------------------------------------------------------

    def _parse(self, raw: str, query: str) -> list[SubQuery]:
        try:
            data = extract_json(raw)
        except json.JSONDecodeError as e:
            logger.error(f"query decomposer returned invalid JSON ({e}); passing query through")
            return [SubQuery(id="sq1", query=query, depends_on=[])]

        items = data.get("subqueries") if isinstance(data, dict) else None
        if not isinstance(items, list) or not items:
            return [SubQuery(id="sq1", query=query, depends_on=[])]

        out: list[SubQuery] = []
        for i, it in enumerate(items[: self.max_subqueries]):
            if not isinstance(it, dict):
                continue
            q = str(it.get("query") or "").strip()
            if not q:
                continue
            sid = str(it.get("id") or f"sq{i + 1}")
            deps = [str(d) for d in (it.get("depends_on") or []) if isinstance(d, (str, int))]
            out.append(SubQuery(id=sid, query=q, depends_on=deps))

        if not out:
            return [SubQuery(id="sq1", query=query, depends_on=[])]
        return out

    @staticmethod
    def _order_by_dependency(subqueries: list[SubQuery]) -> list[SubQuery]:
        """Stable topological order so a dependency always precedes its dependents.
        Falls back to the given order if a cycle/unknown dep is present."""
        by_id = {sq.id: sq for sq in subqueries}
        ordered: list[SubQuery] = []
        placed: set = set()

        def _visit(sq: SubQuery, stack: set) -> None:
            if sq.id in placed or sq.id in stack:
                return
            stack.add(sq.id)
            for dep in sq.depends_on:
                dep_sq = by_id.get(dep)
                if dep_sq is not None:
                    _visit(dep_sq, stack)
            stack.discard(sq.id)
            if sq.id not in placed:
                placed.add(sq.id)
                ordered.append(sq)

        for sq in subqueries:
            _visit(sq, set())
        return ordered

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _build_messages(self, query: str, planner_feedback: str | None = None) -> list[dict]:
        # PLANNER_FEEDBACK is a code word or null, never free text (prompt v4+; older
        # versions ignore the key).
        user_prompt_text = self._prompt.user.format(
            user_query=query, planner_feedback=json.dumps(planner_feedback)
        )
        return [
            {"role": "system", "content": [{"type": "text", "text": self._system_prompt_text}]},
            {"role": "user", "content": [{"type": "text", "text": user_prompt_text}]},
        ]

    def _do_llm_call(self, messages: list[dict], **kwargs) -> str:
        result = self._llm_client.complete(
            messages=messages,
            model_ref=self._model_ref,
            stage="decomposer",
            **stage_call_params(self.model_config, temperature=0.1),
            reasoning_effort=self.model_config.get("reasoning_effort", "low"),
            response_format="json",
            prompt_templates=self._prompt_sig["templates"],
            prompt_version=self._prompt_sig["version"],
            **kwargs,
        )
        return result.content


if __name__ == "__main__":
    decomposer = QueryDecomposer()
    for q in [
        "What was Apple's revenue in fiscal 2023?",
        "Which companies have significant China revenue exposure AND for the top 3 such "
        "companies what are the main supply chain risk factors related to china exposure?",
    ]:
        print(q)
        for sq in decomposer.decompose(q):
            print("  ", sq.to_dict())
        print()
