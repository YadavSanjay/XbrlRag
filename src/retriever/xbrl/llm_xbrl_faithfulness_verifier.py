"""XBRL faithfulness verifier — design doc §2.2 "faithfulness_verifier" step.

    ├──► faithfulness_verifier  (cheap LLM pass: each statement vs its cited facts/chunks)
    │
    ├──► cache_write  (results + answer)
    │
    └──► response

This is the last automated check before a
:class:`~src.retriever.xbrl.llm_xbrl_synthesizer.XbrlRagResponse` is returned to the
caller. For each ``answer_details[]`` entry the synthesizer produced, it:

1. Resolves that statement's own cited evidence — its ``fact_ids`` (matched against
   ``execution_result.steps`` by ``citation.fact_id``, ``step_id``, a ``screen``/
   ``breakdown`` row's ``fact_id`` or ``component_fact_ids`` (docs/specs/
   citation-row-components.md), or a step's ``component_fact_ids`` value) and its
   ``chunk_ids`` (matched against ``narrative_chunks``). A cited screen row's evidence
   carries its ``rank`` and ``period_end``, and every screen the statement cites (by step id
   or by a row) adds its ``screen`` summary: universe, members, ranked filers and their
   count, rows returned, exclusions, period ends and whether they differ
   (:mod:`src.retriever.xbrl.screen_evidence`, docs/specs/verifier-derived-disclosures.md).
   A lookup, compute or aggregate step's evidence carries its ``period``, derived in code
   (:mod:`src.retriever.xbrl.step_period`, docs/specs/verifier-period-evidence.md): a
   lookup's fiscal year, fiscal period and stated start and end, a compute's per-input
   periods, an aggregate's screen fiscal year and member period ends.
2. A statement citing **no** ids at all is deterministically ``UNSUPPORTED`` — no LLM
   call needed. So is a statement citing **any** id that resolves to nothing, and one
   stating a number that matches none of its cited values
   (:mod:`src.retriever.xbrl.citation_integrity`, docs/specs/citation-integrity.md); these
   verdicts carry ``check`` plus ``unresolved_ids`` / ``unmatched_numbers`` and emit a
   ``decision`` event at point ``citation_integrity``.
3. Every other statement is sent, **batched in a single cheap LLM call** (default
   ``gpt-5-nano``), to the verifier prompt: does this statement follow from *only* its
   own evidence?
4. ``UNSUPPORTED`` statements are (by default) dropped from ``answer_details`` and moved
   to ``unanswered_parts`` with the verifier's reason — "void of hallucinations" per the
   caller's requirement. ``PARTIALLY_SUPPORTED``/``SUPPORTED`` statements are kept as-is.
5. ``response.metadata["faithfulness"]`` is always populated with the full per-statement
   report (even when nothing was dropped), and ``metadata["fully_answered"]`` is forced
   to ``False`` if anything is less than fully ``SUPPORTED``.

Note: ``answer_summary`` is **not** re-synthesized (that would be a second, non-"cheap"
LLM call) — if any statement is dropped, a short deterministic caveat sentence is appended
to ``answer_summary`` pointing at ``metadata["faithfulness"]``.
"""
from __future__ import annotations

import copy
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Literal, Optional, Tuple

from prompts.prompt_registry import PromptRegistry, get_xbrl_registry
from src.llm.escalation import emit_escalation, escalation_ladder, is_escalation_trigger
from src.llm.gateway import extract_json, resolve_model_ref, stage_call_params
from src.llm.gateway.model_ref import ModelRef
from src.llm.run_budget import RunHalt
from src.llm.stage_models import require_api_key, resolve_stage
from src.llm.xbrl_gateway import build_xbrl_gateway
from src.logging_config import get_logger
from src.observability.trace_context import log_event
from src.retriever.xbrl.citation_integrity import (
    retrieved_chunks,
    row_component_ids,
    unmatched_numbers,
    unresolved_citation_ids,
)
from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagResponse
from src.retriever.xbrl.screen_evidence import screen_summary

logger = get_logger(__name__)

# Chunk text is truncated to this many chars in the verifier prompt — keeps the
# "cheap LLM pass" cheap even when answer_details cites long table chunks.
DEFAULT_MAX_CHUNK_CHARS = 600

# Statements are verified in batches of at most this many per LLM call. A single
# oversized batch (many statements, each citing long chunk evidence) is what let
# the gpt-5-nano reasoning budget get exhausted and return empty content; capping
# batch size keeps every call small enough to answer within its token budget.
DEFAULT_VERIFIER_BATCH_SIZE = 8

FAITHFULNESS_RESPONSE_FORMAT = {
    "verdicts": [
        {
            "index": "integer - the 0-based 'index' value from the input statement.",
            "verdict": "string - one of 'SUPPORTED', 'PARTIALLY_SUPPORTED', 'UNSUPPORTED'.",
            "reason": "string - one short sentence explaining the verdict.",
        }
    ]
}


class FaithfulnessVerdict(str, Enum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"


class VerifierStatus(str, Enum):
    """Did the faithfulness check actually run to completion?

    Distinguishes a clean pass from a verifier that never produced usable
    output — so "verifier failed" can never be mistaken for "nothing
    unsupported found" (design doc R6 / §3.6).
    """

    OK = "OK"                  # LLM responded and was parsed (verdicts obtained)
    LLM_ERROR = "LLM_ERROR"    # the LLM call itself failed (after gateway retries)
    PARSE_ERROR = "PARSE_ERROR"  # the LLM responded but the JSON could not be parsed


@dataclass
class _VerifierCallResult:
    """Outcome of one batched verifier LLM call."""

    verdicts: dict[int, tuple[FaithfulnessVerdict, str]] = field(default_factory=dict)
    status: VerifierStatus = VerifierStatus.OK
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.status != VerifierStatus.OK


@dataclass
class StatementVerdict:
    index: int
    statement: str
    verdict: FaithfulnessVerdict
    reason: str
    fact_ids: list[str] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)
    # Set when a deterministic citation check decided the verdict (docs/specs/
    # citation-integrity.md): {"check": "unresolved_ids", "unresolved_ids": [...]} or
    # {"check": "number_mismatch", "unmatched_numbers": [...]}.
    citation_check: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "statement": self.statement,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "fact_ids": self.fact_ids,
            "chunk_ids": self.chunk_ids,
            **self.citation_check,
        }


@dataclass
class FaithfulnessReport:
    verdicts: list[StatementVerdict] = field(default_factory=list)

    @property
    def all_supported(self) -> bool:
        return all(v.verdict == FaithfulnessVerdict.SUPPORTED for v in self.verdicts)

    @property
    def unsupported_indices(self) -> list[int]:
        return [v.index for v in self.verdicts if v.verdict == FaithfulnessVerdict.UNSUPPORTED]

    def to_dict(self) -> dict:
        return {
            "all_supported": self.all_supported,
            "verdicts": [v.to_dict() for v in self.verdicts],
        }


class XbrlFaithfulnessVerifier:
    """Verifies each ``answer_details[]`` statement against its own cited evidence.

    Example:
        >>> verifier = XbrlFaithfulnessVerifier()
        >>> checked = verifier.verify(response)
        >>> checked.metadata["faithfulness"]["all_supported"]
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        drop_unsupported: bool = True,
        max_chunk_chars: int = DEFAULT_MAX_CHUNK_CHARS,
        batch_size: int = DEFAULT_VERIFIER_BATCH_SIZE,
        prompt_version: str | None = None,
        prompt_provider: str | None = None,
        prompt_series: str | None = None,
        prompt_registry: PromptRegistry | None = None,
    ):
        # The model comes from the LLM profile active now (an explicit model wins), and
        # the key from that model's provider (docs/specs/llm-provider-profiles.md).
        stage_model = resolve_stage("faithfulness_verifier", model)
        self.api_key = require_api_key(stage_model.model, stage_model.settings, api_key)
        self.model = stage_model.model
        self._llm_settings = stage_model.settings
        self.drop_unsupported = drop_unsupported
        self.max_chunk_chars = max_chunk_chars
        self.batch_size = max(1, int(batch_size))

        self._model_ref = resolve_model_ref(self.model)
        self.model_config = self._model_ref.config
        self._registry = prompt_registry or get_xbrl_registry()
        self._prompt = self._registry.resolve(
            "faithfulness_verifier",
            provider=prompt_provider or self._model_ref.provider,
            series=prompt_series or self._model_ref.series,
            version=prompt_version,
        )
        self._prompt_sig = self._prompt.signature()
        self._llm_client = build_xbrl_gateway("verifier", api_key=api_key)

        logger.info(f"Initialized XBRL Faithfulness Verifier with model={model}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify(self, response: XbrlRagResponse) -> XbrlRagResponse:
        """Check every ``response.answer_details[]`` statement against its own
        cited evidence and return an updated :class:`XbrlRagResponse`.

        No-op (returns *response* unchanged) if ``answer_details`` is empty —
        e.g. the plan-error path, which never reaches the synthesizer.
        """
        if not response.answer_details:
            return response

        fact_by_step_id, fact_by_fact_id = self._index_fact_steps(response.execution_result)
        chunk_by_id = retrieved_chunks(response.narrative_chunks)

        verdicts: dict[int, StatementVerdict] = {}
        llm_entries: list[dict] = []

        for i, detail in enumerate(response.answer_details):
            statement = detail.get("statement", "")
            fact_ids = [str(f) for f in detail.get("fact_ids", []) or []]
            chunk_ids = [str(c) for c in detail.get("chunk_ids", []) or []]

            fact_evidence = self._resolve_fact_evidence(fact_ids, fact_by_step_id, fact_by_fact_id)
            chunk_evidence = self._resolve_chunk_evidence(chunk_ids, chunk_by_id)

            if not fact_ids and not chunk_ids:
                verdicts[i] = StatementVerdict(
                    index=i, statement=statement, verdict=FaithfulnessVerdict.UNSUPPORTED,
                    reason="statement cites no fact_ids or chunk_ids - no evidence to check against",
                    fact_ids=fact_ids, chunk_ids=chunk_ids,
                )
                continue

            citation_verdict = self._check_citations(i, detail, response)
            if citation_verdict is not None:
                verdicts[i] = citation_verdict
                continue

            llm_entries.append({
                "index": i,
                "statement": statement,
                "fact_evidence": fact_evidence,
                "chunk_evidence": chunk_evidence,
            })

        # Verify in bounded batches so no single LLM call is large enough to
        # exhaust a reasoning model's token budget (see DEFAULT_VERIFIER_BATCH_SIZE).
        # A failed batch marks only its own statements unverified; the overall
        # status reflects the worst batch outcome so _apply_report can caveat it.
        overall_status = VerifierStatus.OK
        overall_error: str | None = None
        for batch in self._chunked(llm_entries, self.batch_size):
            call = self._call_llm_with_escalation(batch)
            if call.failed:
                overall_status = call.status
                overall_error = call.error
            for entry in batch:
                i = entry["index"]
                detail = response.answer_details[i]
                fact_ids = [str(f) for f in detail.get("fact_ids", []) or []]
                chunk_ids = [str(c) for c in detail.get("chunk_ids", []) or []]
                v = call.verdicts.get(i)
                if v is None:
                    # Distinguish "verifier ran but omitted this one" from
                    # "verifier never produced output" — same conservative
                    # verdict, but an honest, machine-readable reason.
                    if call.failed:
                        reason = (
                            f"unverified: faithfulness check did not complete "
                            f"({call.status.value})"
                        )
                    else:
                        reason = "verifier returned no verdict for this statement"
                    verdicts[i] = StatementVerdict(
                        index=i, statement=entry["statement"],
                        verdict=FaithfulnessVerdict.PARTIALLY_SUPPORTED,
                        reason=reason,
                        fact_ids=fact_ids, chunk_ids=chunk_ids,
                    )
                else:
                    verdict, reason = v
                    verdicts[i] = StatementVerdict(
                        index=i, statement=entry["statement"],
                        verdict=verdict, reason=reason,
                        fact_ids=fact_ids, chunk_ids=chunk_ids,
                    )

        report = FaithfulnessReport(verdicts=[verdicts[i] for i in sorted(verdicts)])
        return self._apply_report(response, report, overall_status, overall_error)

    @staticmethod
    def _check_citations(
        index: int, detail: dict, response: XbrlRagResponse
    ) -> StatementVerdict | None:
        """Applies the deterministic citation checks to one statement, before any LLM call.

        Rule 1 first: a statement citing any id that resolves to no evidence of this query
        is ``UNSUPPORTED``. Otherwise rule 3: a statement stating a number that matches none
        of its cited evidence is ``UNSUPPORTED`` (docs/specs/citation-integrity.md). Each
        drop emits a ``decision`` event at point ``citation_integrity``.

        Args:
            index: The statement's index in ``answer_details``.
            detail: The ``answer_details`` entry.
            response: The response the statement belongs to.

        Returns:
            The verdict when a check fails, else None (the statement goes to the LLM).
        """
        statement = detail.get("statement", "")
        fact_ids = [str(f) for f in detail.get("fact_ids", []) or []]
        chunk_ids = [str(c) for c in detail.get("chunk_ids", []) or []]
        unresolved = unresolved_citation_ids(
            detail, response.execution_result, response.narrative_chunks)
        if unresolved:
            log_event("decision", point="citation_integrity", check="unresolved_ids",
                      index=index, ids=unresolved)
            return StatementVerdict(
                index=index, statement=statement, verdict=FaithfulnessVerdict.UNSUPPORTED,
                reason=("cites ids that resolve to no evidence of this query: "
                        + ", ".join(unresolved)),
                fact_ids=fact_ids, chunk_ids=chunk_ids,
                citation_check={"check": "unresolved_ids", "unresolved_ids": unresolved},
            )
        unmatched = [n["text"] for n in unmatched_numbers(
            detail, response.execution_result, response.plan, response.narrative_chunks)]
        if unmatched:
            log_event("decision", point="citation_integrity", check="number_mismatch",
                      index=index, numbers=unmatched)
            return StatementVerdict(
                index=index, statement=statement, verdict=FaithfulnessVerdict.UNSUPPORTED,
                reason=("states numbers that match none of its cited evidence: "
                        + ", ".join(unmatched)),
                fact_ids=fact_ids, chunk_ids=chunk_ids,
                citation_check={"check": "number_mismatch", "unmatched_numbers": unmatched},
            )
        return None

    @staticmethod
    def _chunked(entries: list[dict], size: int) -> list[list[dict]]:
        return [entries[i:i + size] for i in range(0, len(entries), size)]

    # ------------------------------------------------------------------
    # Evidence resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _index_fact_steps(execution_result: dict) -> tuple[dict[str, dict], dict[str, dict]]:
        """``{step_id: step_dict}`` and ``{fact_id: step_dict}`` for every
        non-``narrative`` step in ``execution_result['steps']``.

        A ``screen`` or ``breakdown`` step has no single scalar value/citation — it
        carries a ``rows`` list, each row with its own ``fact_id``, ``value`` and
        ``citation`` (a company for a screen, a member for a breakdown). Each row is
        indexed individually (under its own ``fact_id``) as a synthetic per-row
        evidence dict, so a synthesizer statement citing one row's ``fact_id`` is
        checked against *that row* only. The ids a row's ``component_fact_ids`` lists
        resolve to the same entry (docs/specs/citation-row-components.md); they are
        indexed last, so an id that resolves to a record today keeps that record."""
        by_step_id: dict[str, dict] = {}
        by_fact_id: dict[str, dict] = {}
        row_components: list[tuple[str, dict]] = []
        for step in (execution_result or {}).get("steps", []):
            if step.get("op") == "narrative":
                continue
            step_id = step.get("step_id")
            if step_id is not None:
                by_step_id[str(step_id)] = step

            if step.get("op") in ("screen", "breakdown"):
                for row in step.get("rows", []) or []:
                    fid = row.get("fact_id")
                    if fid is None:
                        continue
                    by_fact_id[str(fid)] = {
                        # Unique per-row step_id so a statement citing several
                        # screen rows keeps all of them (dedup is by step_id).
                        "step_id": f"{step_id}:{fid}",
                        "op": step.get("op"),
                        "status": step.get("status"),
                        "value": row.get("value"),
                        "unit": row.get("unit"),
                        "citation": row.get("citation"),
                        "ticker": row.get("ticker"),
                        "fiscal_year": row.get("fiscal_year"),
                        "pct_of_total": row.get("pct_of_total"),
                        "label": row.get("label"),
                    }
                    # A screen row's rank and stated period end, and its parent screen
                    # (docs/specs/verifier-derived-disclosures.md, Requirement 4).
                    if step.get("op") == "screen":
                        by_fact_id[str(fid)].update({
                            "rank": row.get("rank"),
                            "period_end": row.get("period_end"),
                            "screen_step_id": step_id,
                        })
                    row_components += [(c, by_fact_id[str(fid)])
                                       for c in row_component_ids(row)]
                continue

            citation = step.get("citation") or {}
            fact_id = citation.get("fact_id")
            if fact_id is not None:
                by_fact_id[str(fact_id)] = step
            # A derived step's component facts resolve to the step (citation-integrity
            # rule 1), so its evidence matches the deterministic check's.
            for component in (step.get("component_fact_ids") or {}).values():
                if component is not None:
                    by_fact_id.setdefault(str(component), step)
        for component, entry in row_components:
            by_fact_id.setdefault(component, entry)
        return by_step_id, by_fact_id

    @staticmethod
    def _resolve_fact_evidence(
        fact_ids: list[str], by_step_id: dict[str, dict], by_fact_id: dict[str, dict]
    ) -> list[dict]:
        """Resolve each ``answer_details[].fact_ids`` entry to its step result —
        ``fact_id`` (lookup citation) is tried first, then ``step_id`` (compute)."""
        evidence: list[dict] = []
        seen: set = set()
        for fid in fact_ids:
            step = by_fact_id.get(fid) or by_step_id.get(fid)
            if step is None:
                continue
            key = step.get("step_id")
            if key in seen:
                continue
            seen.add(key)
            ev = {
                "step_id": step.get("step_id"),
                "op": step.get("op"),
                "status": step.get("status"),
                "value": step.get("value"),
                "unit": step.get("unit"),
                "citation": step.get("citation"),
            }
            # Screen/breakdown rows carry extra context a statement may assert (the
            # company or member, fiscal year, share of total) — surface it so those
            # claims can be verified rather than flagged as unevidenced.
            for extra in ("ticker", "fiscal_year", "pct_of_total", "label", "rank",
                          "period_end"):
                if step.get(extra) is not None:
                    ev[extra] = step.get(extra)
            # An aggregate step's value is computed in code over a screen's rows
            # (docs/specs/screen-aggregates.md): show what it covers, so a stated total,
            # median or count of the group is checked against it, not flagged unevidenced.
            aggregate = step.get("aggregate")
            if step.get("op") == "aggregate" and isinstance(aggregate, dict):
                ev["aggregate"] = {
                    key: aggregate.get(key)
                    for key in ("fn", "n", "members", "fact_ids", "middle", "universe",
                                "universe_members", "exclusions")
                }
            # A cagr step's rate is computed in code from two lookups
            # (docs/specs/plan-cagr-and-ratios.md): show its years, inputs and their facts,
            # so a stated CAGR (and its two inputs) is checked against them.
            cagr = step.get("cagr")
            if step.get("op") == "cagr" and isinstance(cagr, dict):
                ev["cagr"] = {
                    key: cagr.get(key)
                    for key in ("start", "end", "start_fy", "end_fy", "years", "start_value",
                                "end_value", "fact_ids")
                }
                if step.get("error"):
                    ev["error"] = step.get("error")
            # A compute or cagr comparing periods of different lengths: the synthesizer
            # states each entry's note in the value's statement
            # (docs/specs/period-year-length-answers.md), so show it as evidence.
            year_lengths = step.get("year_lengths")
            if step.get("op") in ("compute", "cagr") and isinstance(year_lengths, list):
                ev["year_lengths"] = year_lengths
            # A lookup's, compute's or aggregate's fiscal year and period, derived in code
            # from the facts it cites (docs/specs/verifier-period-evidence.md, Requirement 4).
            period = step.get("period")
            if step.get("op") in ("lookup", "compute", "aggregate") and isinstance(
                    period, Mapping):
                ev["period"] = copy.deepcopy(dict(period))
            evidence.append(ev)
        # What each cited screen derived in code (docs/specs/verifier-derived-disclosures.md,
        # Requirement 4.2): one ``screen`` entry per screen the statement cites, by its step
        # id (on the step's own entry) or by any of its rows (an entry of its own).
        for screen_step in XbrlFaithfulnessVerifier._cited_screens(fact_ids, by_step_id):
            summary = screen_summary(screen_step)
            own = next((ev for ev in evidence if ev.get("op") == "screen"
                        and ev.get("step_id") == screen_step.get("step_id")), None)
            if own is not None:
                own["screen"] = summary
            else:
                evidence.append({"step_id": screen_step.get("step_id"), "op": "screen",
                                 "status": screen_step.get("status"), "screen": summary})
        return evidence

    @staticmethod
    def _cited_screens(fact_ids: list[str], by_step_id: dict[str, dict]) -> list[dict]:
        """The screen steps a statement cites, by step id or a row's fact id, in first-cited order.

        Args:
            fact_ids: The statement's ``fact_ids`` as strings.
            by_step_id: ``{step_id: step_dict}`` from :meth:`_index_fact_steps`.

        Returns:
            Each cited screen step once.
        """
        screens = [s for s in by_step_id.values() if s.get("op") == "screen"]
        cited: list[dict] = []
        for fid in fact_ids:
            for step in screens:
                if any(step is c for c in cited):
                    continue
                row_ids = {str(r.get("fact_id")) for r in step.get("rows", []) or []
                           if r.get("fact_id") is not None}
                row_ids.update(c for r in step.get("rows", []) or []
                               for c in row_component_ids(r))
                if str(step.get("step_id")) == fid or fid in row_ids:
                    cited.append(step)
        return cited

    def _resolve_chunk_evidence(self, chunk_ids: list[str], chunk_by_id: dict[str, dict]) -> list[dict]:
        evidence: list[dict] = []
        for cid in chunk_ids:
            chunk = chunk_by_id.get(cid)
            if chunk is None:
                continue
            text = chunk.get("text", "") or ""
            if len(text) > self.max_chunk_chars:
                text = text[: self.max_chunk_chars] + " …"
            evidence.append({
                "chunk_id": cid,
                "text": text,
                "section_path": chunk.get("section_path"),
            })
        return evidence

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _call_llm_with_escalation(self, entries: list[dict]) -> _VerifierCallResult:
        """Run one batch, escalating a rung on an unparseable response (design §3.3).

        A cheap ``gpt-5-nano`` verifier that returns unparseable JSON for a batch
        is retried one rung up (``gpt-5-mini``); only if that also fails does the
        batch stay a distinct non-OK status (its statements marked unverified).
        """
        refs = escalation_ladder("faithfulness_verifier", self._model_ref, settings=self._llm_settings)
        call = _VerifierCallResult(status=VerifierStatus.LLM_ERROR, error="no attempt")
        for i, ref in enumerate(refs):
            call = self._call_llm(entries, model_ref=ref)
            if not call.failed:
                return call
            reason = "parse_error" if call.status == VerifierStatus.PARSE_ERROR else "llm_error"
            if i + 1 < len(refs) and is_escalation_trigger("faithfulness_verifier", reason):
                emit_escalation("faithfulness_verifier", ref, refs[i + 1], reason, attempt=i + 1)
                continue
            break
        return call

    def _call_llm(self, entries: list[dict], model_ref: ModelRef | None = None) -> _VerifierCallResult:
        """Run the batched verifier call, returning verdicts *and* a status.

        A failed call (LLM error after gateway retries) or an unparseable
        response is reported as a distinct, non-OK status rather than silently
        collapsing to "no verdicts" — the caller treats those statements as
        *unverified*, never as a clean pass (R6).
        """
        messages = self._build_messages(entries)
        logger.debug(f"\n\n XBRL faithfulness verifier messages prepared for LLM call: {messages}\n\n")
        try:
            raw = self._do_llm_call(messages, model_ref=model_ref)
        except RunHalt:
            raise  # the run is stopping, not the call failing (docs/specs/xbrlrag-run-halt.md)
        except Exception as e:  # gateway already retried transient errors
            logger.error(f"Faithfulness verifier LLM call failed: {e}")
            return _VerifierCallResult(status=VerifierStatus.LLM_ERROR, error=repr(e))

        logger.info(f"XBRL faithfulness verifier LLM response: {raw}")
        try:
            parsed = extract_json(raw)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse faithfulness verifier response as JSON: {e}")
            return _VerifierCallResult(status=VerifierStatus.PARSE_ERROR, error=str(e))

        if not isinstance(parsed, dict):
            logger.error("faithfulness verifier returned non-object JSON")
            return _VerifierCallResult(status=VerifierStatus.PARSE_ERROR, error="non-object JSON")

        results: dict[int, tuple[FaithfulnessVerdict, str]] = {}
        for v in parsed.get("verdicts", []):
            try:
                idx = int(v.get("index"))
                verdict = FaithfulnessVerdict(v.get("verdict"))
            except (TypeError, ValueError):
                logger.warning(f"skipping malformed faithfulness verdict: {v!r}")
                continue
            results[idx] = (verdict, str(v.get("reason", "")))
        return _VerifierCallResult(verdicts=results, status=VerifierStatus.OK)

    def _build_messages(self, entries: list[dict]) -> list[dict]:
        system_prompt_text = self._prompt.system.format(
            llm_answer_format=FAITHFULNESS_RESPONSE_FORMAT,
        )
        user_prompt_text = self._prompt.user.format(
            statements_json=json.dumps(entries, indent=2, default=str),
        )
        return [
            {"role": "system", "content": [{"type": "text", "text": system_prompt_text}]},
            {"role": "user", "content": [{"type": "text", "text": user_prompt_text}]},
        ]

    def _do_llm_call(self, messages: list[dict], model_ref: ModelRef | None = None, **kwargs) -> str:
        """Run the faithfulness-verifier LLM call through the instrumented gateway.

        Latency, token usage / cost, transient-error retry and the structured
        ``llm_call`` event are handled by :class:`~src.llm.gateway.LLMGateway`.
        ``model_ref`` overrides the model (escalation); its own per-call config is
        used so an escalated model runs with the right token/temperature budget.
        """
        ref = model_ref or self._model_ref
        cfg = ref.config or self.model_config
        result = self._llm_client.complete(
            messages=messages,
            model_ref=ref,
            stage="verifier",
            **stage_call_params(cfg, temperature=0.1),
            reasoning_effort=cfg.get("reasoning_effort", "low"),
            response_format="json",
            prompt_templates=self._prompt_sig["templates"],
            prompt_version=self._prompt_sig["version"],
            **kwargs,
        )
        return result.content

    # ------------------------------------------------------------------
    # Apply report
    # ------------------------------------------------------------------

    def _apply_report(
        self,
        response: XbrlRagResponse,
        report: FaithfulnessReport,
        verifier_status: VerifierStatus = VerifierStatus.OK,
        verifier_error: str | None = None,
    ) -> XbrlRagResponse:
        data = response.model_dump()

        kept_details: list[dict] = []
        unanswered_parts: list[dict] = list(data.get("unanswered_parts") or [])
        dropped: list[StatementVerdict] = []

        for v in report.verdicts:
            detail = data["answer_details"][v.index]
            if self.drop_unsupported and v.verdict == FaithfulnessVerdict.UNSUPPORTED:
                dropped.append(v)
                unanswered_parts.append({
                    "question_fragment": detail.get("statement", ""),
                    "reason": f"failed faithfulness check: {v.reason}",
                })
            else:
                kept_details.append(detail)

        data["answer_details"] = kept_details
        data["unanswered_parts"] = unanswered_parts

        verifier_failed = verifier_status != VerifierStatus.OK

        metadata = dict(data.get("metadata") or {})
        faithfulness = report.to_dict()
        # Record whether the check actually completed, so downstream consumers
        # never read a verifier failure as a clean pass (R6).
        faithfulness["status"] = verifier_status.value
        if verifier_error:
            faithfulness["error"] = verifier_error
        metadata["faithfulness"] = faithfulness
        # Not fully answered if anything is less than SUPPORTED *or* the check
        # could not be completed at all.
        if not report.all_supported or verifier_failed:
            metadata["fully_answered"] = False
        data["metadata"] = metadata

        caveats: list[str] = []
        if dropped:
            caveats.append(
                f" (Note: {len(dropped)} statement(s) were removed after failing automated "
                "fact-checking - see metadata.faithfulness for details.)"
            )
        if verifier_failed:
            caveats.append(
                " (Note: automated fact-checking could not be completed "
                f"({verifier_status.value}); treat figures as unverified - "
                "see metadata.faithfulness.)"
            )
        if caveats:
            data["answer_summary"] = (data.get("answer_summary") or "").rstrip() + "".join(caveats)

        if verifier_failed:
            log_event(
                "verifier_error",
                status=verifier_status.value,
                error=verifier_error,
                statements_checked=len(report.verdicts),
            )
            logger.warning(
                "Faithfulness verification did not complete (status=%s): %s",
                verifier_status.value, verifier_error,
            )

        return XbrlRagResponse(**data)
