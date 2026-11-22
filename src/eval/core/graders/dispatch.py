"""WS-9 / E1 — grade one ``(EvalCase, SystemAnswer)`` pair.

Dispatches by ``case.kind`` to the scalar / screen / narrative path and folds in the
grounding check. Pure: no DB, no LLM, no network — every grader in Phases 0–2 is
deterministic (numeric tolerance, fact-id set comparison, rank correlation), which
keeps runs cheap, reproducible, and free of the doer≠judge problem. The LLM judge
is scoped to narrative in Phase 3 (design doc §7.4).

Grade ladder for a scalar case:

===========================================  ==========
outcome                                      grade
===========================================  ==========
system raised                                ERROR
value matched + grounding satisfied          CORRECT
value matched, citation missing/insufficient PARTIAL
value produced but no match, hedged          HEDGED_MISS
v2: no match, right at its own precision     IMPRECISE
value produced but no match                  INCORRECT
nothing produced + refusal signal            REFUSED
nothing produced, no refusal signal          INCORRECT
===========================================  ==========
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any, Dict, List

from src.eval.core.answer_parsing import (
    StatedNumber,
    extract_stated_numbers,
    graded_values,
    synthesis_failure,
    undisclosed_values,
    unexempt_values,
)
from src.eval.core.case import KIND_BREAKDOWN, KIND_NARRATIVE, KIND_SCREEN, EvalCase
from src.eval.core.graders.base import CaseGrade, Grade
from src.eval.core.graders.breakdown import breakdown_verdict, compare_breakdown
from src.eval.core.graders.citation import grade_grounding
from src.eval.core.graders.numeric import close_enough, match_reading, matches_any
from src.eval.core.graders.precision import (
    AMOUNT,
    LEGACY,
    PER_SHARE,
    PrecisionRule,
    is_income_metric,
    matches_at_precision,
    precision_outcome,
    precision_rule,
)
from src.eval.core.graders.screen import compare_screen, row_value, screen_verdict
from src.eval.core.system import SystemAnswer


def _expected_payload(case: EvalCase) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if case.expected_value is not None:
        payload["value"] = case.expected_value
    if case.alt_values:
        payload["alt_values"] = case.alt_values
    if case.expected_rows is not None:
        payload["rows"] = case.expected_rows
    if case.expected_text is not None:
        payload["text"] = case.expected_text
    if case.expected_fact_ids:
        payload["fact_ids"] = case.expected_fact_ids
    if case.expected_chunk_ids:
        payload["chunk_ids"] = case.expected_chunk_ids
    return payload


def _base(
    case: EvalCase, answer: SystemAnswer, grade: Grade, passed: bool, reason: str
) -> CaseGrade:
    # raw_numeric_values: the answer's values as this grader received them; grade_case
    # replaces them with the values it received, before any filter
    # (docs/specs/regrade-raw-values.md, requirement 1).
    produced = answer.to_dict()
    produced["raw_numeric_values"] = list(answer.numeric_values)
    return CaseGrade(
        case_id=case.id,
        grade=grade,
        passed=passed,
        reason=reason,
        expected=_expected_payload(case),
        produced=produced,
        has_citation=bool(answer.fact_ids),
        error=answer.error,
    )


def _refused(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    return _base(
        case,
        answer,
        Grade.REFUSED,
        False,
        "system declined to answer (no audited value produced)",
    )


def _grade_not_meaningful(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """Grade a percentage change whose base is <= 0: "n/m" is the right answer.

    Declining is correct even when the answer shows the two component values; any
    number that is the ratio itself (±v or ±100·v under the |base| convention) is a
    percentage claim and wrong, hedged or not.
    """
    claims = [s * v for v in case.targets() for s in (1.0, -1.0, 100.0, -100.0)]
    stated = next(
        (x for x in answer.numeric_values if matches_any(x, claims, case.rel_tol)), None
    )
    if stated is not None:
        result = _base(
            case, answer, Grade.INCORRECT, False,
            f"stated {stated} for a percentage change that is not meaningful (n/m)",
        )
        result.matched_value = stated
        return result
    if answer.refused or answer.hedged:
        return _base(case, answer, Grade.CORRECT, True, "declined: not meaningful (n/m)")
    result = _base(
        case, answer, Grade.INCORRECT, False,
        "asserted an answer where the percentage change is not meaningful (n/m)",
    )
    # Real component facts, not a false percentage: a relevance miss, kept off the
    # silent-wrong gate the same way an incomplete breakdown is.
    result.incomplete = bool(answer.numeric_values)
    return result


def _describe(rule: PrecisionRule) -> str:
    """A rule in a reason: "quantum 1e+06", "sig_figs 3", "exact", "rel_tol 0.0001"."""
    if rule.compare == "quantum":
        return f"quantum {rule.quantum:g}"
    if rule.compare == "sig_figs":
        return f"{rule.sig_figs} significant figures"
    if rule.compare == "rel_tol":
        return f"rel_tol {rule.rel_tol:g}"
    return rule.compare


def _stated_quantum(value: float, stated: list[StatedNumber]) -> float | None:
    """The quantum of the first stated number equal to a produced value, if any."""
    for number in stated:
        if (value == 0 and number.value == 0) or (
                number.value != 0 and close_enough(value, number.value, 1e-12)):
            return number.quantum
    return None


def _match_at_precision(
    case: EvalCase, answer: SystemAnswer, rule: PrecisionRule
) -> tuple[float | None, bool]:
    """A v2 scalar's first produced value that matches at its rule, and whether some
    produced value is only imprecise (docs/specs/grading-precision.md, req. 8 and 9).

    For an income-type lookup a value a loss word governs is read as ``-|v|``.

    Returns:
        ``(matched value or None, any imprecise)``.
    """
    if case.expected_value is None:
        return None, False
    stated = extract_stated_numbers(answer.text or "")
    losses = [n.value for n in stated if n.loss]
    sign_losses = rule.kind in (AMOUNT, PER_SHARE) and bool(losses) and is_income_metric(case)
    imprecise = False
    for produced in answer.numeric_values:
        value = produced
        if sign_losses and any(
                (produced == 0 and v == 0) or (v != 0 and close_enough(produced, v, 1e-12))
                for v in losses):
            value = -abs(produced)
        outcome = precision_outcome(
            value, _stated_quantum(produced, stated), case.expected_value, rule
        )
        if outcome == "match":
            return value, imprecise
        imprecise = imprecise or outcome == "imprecise"
    return None, imprecise


def grade_scalar(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """Grade a ``lookup`` / ``compute`` / ``dimensional`` case."""
    if case.expect_not_meaningful:
        return _grade_not_meaningful(case, answer)
    targets = case.targets()
    produced = answer.numeric_values

    if not produced:
        if answer.refused or answer.hedged:
            return _refused(case, answer)
        return _base(
            case, answer, Grade.INCORRECT, False, "no audited value in execution_result"
        )

    if not targets:
        return _base(
            case,
            answer,
            Grade.PARTIAL,
            False,
            "case carries no expected value — nothing to compare against",
        )

    rule = precision_rule(case)
    imprecise = False
    if rule.kind == LEGACY or case.expected_value is None:
        matched = next((v for v in produced if matches_any(v, targets, case.rel_tol)), None)
    else:
        matched, imprecise = _match_at_precision(case, answer, rule)
    reading: str | None = "key" if matched is not None else None
    if matched is None and case.alt_readings:
        # A disclosed alternate reading counts as the key does, each within rel_tol on
        # its own; the key wins (docs/specs/shares-outstanding-reading.md requirement 7).
        for value in produced:
            reading = match_reading(value, [], case.alt_readings, case.rel_tol)
            if reading is not None:
                matched = value
                imprecise = False
                break
    grounding = grade_grounding(
        case.expected_fact_ids,
        answer.fact_ids,
        require_exact=case.require_exact_fact,
        fact_id_classes=case.grounding_classes(),
    )

    result = _base(case, answer, Grade.INCORRECT, False, "")
    result.matched_value = matched
    if case.alt_readings and reading is not None:
        result.matched_readings = {"value": reading}
    result.grounding_precision = grounding.precision
    result.grounding_recall = grounding.recall

    if matched is None:
        result.reason = (
            f"no produced value within rel_tol={case.rel_tol:g} of "
            f"{case.expected_value}; produced={produced}"
        )
        if rule.kind != LEGACY:
            result.reason = (
                f"no produced value matches {case.expected_value} at its "
                f"{_describe(rule)}; produced={produced}"
            )
        if answer.hedged and not answer.refused:
            # It said it could not answer and showed the nearest facts it had: a
            # decline that carries numbers, not an asserted wrong value.
            result.grade = Grade.HEDGED_MISS
            result.reason = f"hedged: {result.reason}"
        elif imprecise and not answer.hedged:
            # Right at the precision it was stated to, but coarser than the key's rule:
            # neither correct nor wrong (docs/specs/grading-precision.md requirement 8).
            result.grade = Grade.IMPRECISE
            result.reason = f"imprecise: {result.reason}"
        return result

    if case.require_citation and not answer.fact_ids:
        result.grade = Grade.PARTIAL
        result.reason = "value matched but the answer carries no fact_id citation"
        return result

    if case.expected_fact_ids and not grounding.satisfied:
        result.grade = Grade.PARTIAL
        result.reason = (
            f"value matched but grounding is wrong — missing fact_ids "
            f"{grounding.missing}, extra {grounding.extra}"
        )
        return result

    result.grade = Grade.CORRECT
    result.passed = True
    result.reason = "ok"
    return result


def row_matcher(case: EvalCase) -> Callable[[dict, float], bool] | None:
    """How a produced value is matched with one expected row of a ``shape:`` case.

    Each row is compared under its own precision rule (docs/specs/grading-precision.md
    requirement 6). None for a case without a ``shape:`` tag, which keeps ``rel_tol``.

    Args:
        case: A screen, trend or other row-graded case.

    Returns:
        ``match(expected_row, produced_value) -> bool``, or None.
    """
    if precision_rule(case).kind == LEGACY:
        return None

    def match(row: dict, value: float) -> bool:
        expected = row_value(row)
        return expected is not None and matches_at_precision(
            value, expected, precision_rule(case, row)
        )

    return match


def grade_screen(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """Grade a cross-filer ranked ``screen`` case."""
    if not answer.rows:
        if answer.refused or answer.hedged:
            return _refused(case, answer)
        return _base(
            case, answer, Grade.INCORRECT, False, "no screen rows in execution_result"
        )

    metrics = compare_screen(
        case.expected_rows or [], answer.rows, top_n=case.top_n, rel_tol=case.rel_tol,
        value_match=row_matcher(case),
    )
    passed, reason = screen_verdict(metrics, order_sensitive=case.order_sensitive)
    grounding = grade_grounding(
        case.expected_fact_ids,
        answer.fact_ids,
        require_exact=case.require_exact_fact,
        fact_id_classes=case.grounding_classes(),
    )

    result = _base(
        case, answer, Grade.CORRECT if passed else Grade.INCORRECT, passed, reason
    )
    result.screen_metrics = metrics.to_dict()
    result.grounding_precision = grounding.precision
    result.grounding_recall = grounding.recall

    if passed and case.require_citation and not answer.fact_ids:
        result.grade, result.passed = Grade.PARTIAL, False
        result.reason = "ranking matched but no row carries a fact_id citation"
    elif passed and case.expected_fact_ids and not grounding.satisfied:
        result.grade, result.passed = Grade.PARTIAL, False
        result.reason = (
            f"ranking matched but grounding is wrong — missing {grounding.missing}"
        )
    return result


def grade_breakdown(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """Grade a single-filer breakdown (revenue by segment / by geography).

    The planner emits one ``lookup`` per component, so the evidence is the set of
    audited scalar values — not ranked rows. Order is irrelevant: a breakdown is a
    set, not a ranking.
    """
    if not answer.numeric_values:
        if answer.refused or answer.hedged:
            return _refused(case, answer)
        return _base(
            case,
            answer,
            Grade.INCORRECT,
            False,
            "no audited values in execution_result",
        )

    metrics = compare_breakdown(
        case.expected_rows or [],
        answer.numeric_values,
        expected_fact_ids=case.expected_fact_ids,
        produced_fact_ids=answer.fact_ids,
        rel_tol=case.rel_tol,
    )
    passed, reason = breakdown_verdict(metrics)
    grounding = grade_grounding(
        case.expected_fact_ids,
        answer.fact_ids,
        require_exact=case.require_exact_fact,
        fact_id_classes=case.grounding_classes(),
    )

    result = _base(
        case, answer, Grade.CORRECT if passed else Grade.INCORRECT, passed, reason
    )
    result.screen_metrics = metrics.to_dict()
    result.grounding_precision = grounding.precision
    result.grounding_recall = grounding.recall
    # A coverage miss means the answer decomposed differently or covered less than
    # asked — not that it asserted a false number.
    result.incomplete = not passed

    if passed and case.require_citation and not answer.fact_ids:
        result.grade, result.passed = Grade.PARTIAL, False
        result.reason = "components resolved but the answer carries no fact_id citation"
    return result


def grade_narrative(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """Grade a narrative case on retrieval only.

    Deterministic chunk recall is all we can honestly assert without an LLM judge
    (Phase 3). When the case carries no ``expected_chunk_ids`` there is nothing to
    check, so the verdict is ``PARTIAL`` with an explicit reason rather than a
    fabricated pass — an ungraded case must never inflate accuracy.
    """
    if not case.expected_chunk_ids:
        return _base(
            case,
            answer,
            Grade.PARTIAL,
            False,
            "narrative grading needs an LLM judge (Phase 3) or expected_chunk_ids",
        )

    if not answer.chunk_ids:
        if answer.refused:
            return _refused(case, answer)
        return _base(
            case, answer, Grade.INCORRECT, False, "no narrative chunks retrieved"
        )

    expected = set(case.expected_chunk_ids)
    produced = set(answer.chunk_ids)
    hit = expected & produced
    recall = len(hit) / len(expected)

    result = _base(case, answer, Grade.INCORRECT, False, "")
    result.grounding_recall = recall
    result.grounding_precision = len(hit) / len(produced) if produced else 0.0
    if recall > 0:
        result.grade, result.passed = Grade.CORRECT, True
        result.reason = f"retrieved {len(hit)}/{len(expected)} expected chunks"
    else:
        result.reason = f"none of the {len(expected)} expected chunks retrieved"
    return result


def grade_case(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """Grade any case. Never raises — a grading failure is a case failure.

    The grade's ``produced`` also carries ``raw_numeric_values``: the answer's values as
    received, before any grader filter, so a later re-grade starts from them
    (docs/specs/regrade-raw-values.md, requirement 1).
    """
    result = _grade_case(case, answer)
    result.produced["raw_numeric_values"] = list(answer.numeric_values)
    return result


def _grade_case(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """``grade_case`` without the raw values."""
    # Imported here: v2 builds on this module's graders, so a module-level import would
    # be circular.
    from src.eval.core.graders.v2 import grade_v2, v2_subkind

    if answer.error:
        return _base(case, answer, Grade.ERROR, False, f"system error: {answer.error}")
    # A failed synthesis grades alike for every kind, as the baseline arms grade the same
    # failure (docs/specs/grader-synthesis-error.md requirement 2).
    failure = synthesis_failure(answer.text or "")
    if failure == "error":
        grade = _base(case, answer, Grade.ERROR, False,
                      "synthesis failed: the pipeline returned its fallback answer")
        grade.error = "SynthesisFailed('the synthesis reply was unusable; fallback answer')"
        return grade
    if failure == "refusal":
        return _base(case, answer, Grade.REFUSED, False,
                     "synthesis refused: the model declined the synthesis step")
    if v2_subkind(case) is not None:
        return grade_v2(case, answer)
    return grade_legacy(case, answer)


def graded_answer(answer: SystemAnswer) -> SystemAnswer:
    """The answer as a grader reads it: period-length, rank, date-part and disclosure values
    dropped.

    Spec: docs/specs/grading-dont-care-and-ties.md (requirement 7),
    docs/specs/grader-synthesis-error.md (requirement 4) and
    docs/specs/grader-v9-number-reading.md (requirement 4). Idempotent; rows and every
    other field are unchanged.

    Args:
        answer: The system's answer, as stored.

    Returns:
        ``answer`` itself when nothing is dropped, else a copy with ``numeric_values``
        replaced by ``undisclosed_values(text, graded_values(text,
        unexempt_values(text, numeric_values)))``.
    """
    text = answer.text or ""
    values = undisclosed_values(
        text, graded_values(text, unexempt_values(text, answer.numeric_values))
    )
    if values == list(answer.numeric_values):
        return answer
    return dataclasses.replace(answer, numeric_values=values)


def grade_legacy(case: EvalCase, answer: SystemAnswer) -> CaseGrade:
    """Grades a case by its kind alone, as every case was graded before v2.

    Args:
        case: Any eval case.
        answer: The system's answer.

    Returns:
        The scalar, screen, breakdown or narrative grade. A ``compute.yoy_growth`` scalar
        case is also offered the absolute-change reading
        (docs/specs/pick-skips-questions.md requirement 5(a)).
    """
    # Imported here: v2 builds on this module's graders (see _grade_case).
    from src.eval.core.graders.v2 import grade_growth_scalar

    answer = graded_answer(answer)
    if case.kind == KIND_SCREEN:
        return grade_screen(case, answer)
    if case.kind == KIND_BREAKDOWN:
        return grade_breakdown(case, answer)
    if case.kind == KIND_NARRATIVE:
        return grade_narrative(case, answer)
    return grade_growth_scalar(case, answer)


def grade_all(cases: list[EvalCase], answers: list[SystemAnswer]) -> list[CaseGrade]:
    return [grade_case(c, a) for c, a in zip(cases, answers)]
