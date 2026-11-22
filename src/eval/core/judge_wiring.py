# The claim judge inside an eval run: key points, claims, scores and the summary gate.
#
# run_graded runs a suite like EvalRunner.run and also judges each text-graded case once
# with a ClaimJudge (never from the generator profile's family), charges the judge to the
# run's cost budget, records the judge version, and runs the key-free metamorphic pass over
# the run's own answers. judge_summary reports the judge's means only when its version has
# passed calibration. Spec: docs/specs/judge-runner-wiring.md.

from __future__ import annotations

import json
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from src.eval.core.answer_parsing import answer_prose, synthesis_failure
from src.eval.core.case import EvalCase
from src.eval.core.graders.judge import (
    CalibrationReport,
    Claim,
    ClaimJudge,
    JudgeResult,
    model_family,
    scores_reportable,
)
from src.eval.core.graders.numeric import component_fact_ids
from src.eval.core.graders.v2 import text_part, v2_subkind
from src.eval.core.report import CaseResult, RunReport
from src.eval.core.run_columns import JUDGE_COLUMNS
from src.eval.core.runner import EvalRunner, RunnerConfig, _Budget
from src.eval.core.system import SystemAnswer, SystemUnderTest
from src.eval.keyfree.runner import metamorphic_pass
from src.llm.profiles import load_committed_settings
from src.observability.trace_context import trace_query

_SCORE_KEYS = tuple(JUDGE_COLUMNS)


def _field(obj: Any, key: str) -> Any:
    """Reads *key* from a dict, or the attribute of that name from any other object."""
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _id_str(value: Any) -> str | None:
    """A citation id compared as ``str(id).strip()``; ``None`` stays ``None``."""
    return None if value is None else str(value).strip()


def case_key_points(case: EvalCase) -> list[str]:
    """The reference answer's key points of a case.

    Args:
        case: The case, as read from a suite (``key_points`` in its JSON).

    Returns:
        A new list of the key points; ``[]`` when the case has none.
    """
    return list(getattr(case, "key_points", None) or [])


def is_text_graded(case: EvalCase) -> bool:
    """Whether a case is judged: non-blank ``expected_text`` and at least one key point.

    A compound is judged exactly when it has a text part
    (docs/specs/compound-text-part.md requirement 3).
    """
    if v2_subkind(case) == "compound":
        return text_part(case) is not None
    text = case.expected_text
    return isinstance(text, str) and bool(text.strip()) and bool(case_key_points(case))


def _judged_case(case: EvalCase) -> EvalCase:
    """The case whose reference answer and key points the judge reads: a compound's text
    part, else the case itself."""
    part = text_part(case) if v2_subkind(case) == "compound" else None
    return case if part is None else part


def _fact_passage(fact_id: str, ok_steps: list[dict]) -> str | None:
    """The passage a cited fact id resolves to: an OK step, else a row of an OK step.

    A row is cited by its ``fact_id`` or by any id its ``component_fact_ids`` lists
    (docs/specs/citation-row-components.md, Requirement 2.4).
    """
    for step in ok_steps:
        citation = step.get("citation")
        cited = citation.get("fact_id") if isinstance(citation, Mapping) else None
        if fact_id in (_id_str(step.get("step_id")), _id_str(cited)):
            return json.dumps(step, sort_keys=True, default=str)
    for step in ok_steps:
        rows = step.get("rows")
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping):
                continue
            row_ids = [row.get("fact_id"), *component_fact_ids(row.get("component_fact_ids"))]
            if fact_id in {_id_str(i) for i in row_ids if i is not None}:
                return json.dumps(row, sort_keys=True, default=str)
    return None


def _chunk_passage(chunk_id: Any, chunks: list[Any]) -> str | None:
    """The text of the first narrative chunk whose ``chunk_id`` (as str) equals *chunk_id*."""
    wanted = str(chunk_id)
    for chunk in chunks:
        if str(_field(chunk, "chunk_id")) == wanted:
            text = _field(chunk, "text")
            return text if isinstance(text, str) else None
    return None


def answer_claims(answer: SystemAnswer) -> list[Claim]:
    """Turns an answer's statements and their resolved citations into judge claims.

    Args:
        answer: The system's answer; ``answer.raw`` is an ``XbrlRagResponse``-like object
            or dict, or ``None``.

    Returns:
        One claim per non-blank statement (id ``s<index>``), with the text of each passage
        it cites; one uncited claim of the text when there are no structured statements
        (a baseline's JSON reply contributes its ``"text"``, judge-baseline-claim-text).
    """
    raw = answer.raw
    details = _field(raw, "answer_details")
    if not isinstance(details, list):
        text = answer_prose(answer.text) if isinstance(answer.text, str) else answer.text
        return [Claim("s0", text, [])] if isinstance(text, str) and text.strip() else []

    steps = _field(_field(raw, "execution_result"), "steps")
    ok_steps = [
        s for s in (steps if isinstance(steps, list) else [])
        if isinstance(s, dict) and s.get("status") == "OK"
    ]
    chunks = _field(raw, "narrative_chunks")
    chunk_list = chunks if isinstance(chunks, list) else []

    claims: list[Claim] = []
    for i, entry in enumerate(details):
        if not isinstance(entry, Mapping):
            continue
        statement = entry.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            continue
        passages: list[str] = []
        fact_ids = entry.get("fact_ids")
        for fact_id in fact_ids if isinstance(fact_ids, list) else []:
            key = _id_str(fact_id)
            passage = None if key is None else _fact_passage(key, ok_steps)
            if passage is not None and passage not in passages:
                passages.append(passage)
        chunk_ids = entry.get("chunk_ids")
        for chunk_id in chunk_ids if isinstance(chunk_ids, list) else []:
            passage = _chunk_passage(chunk_id, chunk_list)
            if passage is not None and passage not in passages:
                passages.append(passage)
        claims.append(Claim(f"s{i}", statement, passages))
    return claims


def judge_case(judge: ClaimJudge, case: EvalCase, answer: SystemAnswer) -> JudgeResult | None:
    """Judges one case's answer, or returns ``None`` when the case is not judged.

    Args:
        judge: The claim judge.
        case: The case; judged only when text-graded.
        answer: The system's answer; not judged when it has an error or is the
            synthesizer's fallback (``judge_skip_reason``).

    Returns:
        The judge's result, or ``None`` (no call made).

    Raises:
        RunHalt: When the run's budget or circuit breaker stops the judge call.
    """
    if judge_skip_reason(case, answer) is not None:
        return None
    reference = _judged_case(case)
    return judge.judge(
        case.question,
        str(reference.expected_text),
        case_key_points(reference),
        answer_claims(answer),
    )


def judge_skip_reason(case: EvalCase, answer: SystemAnswer) -> str | None:
    """Why a case's answer is not sent to the judge, or None when it is judged.

    Spec: docs/specs/grader-v9-number-reading.md (requirement 5).

    Args:
        case: The case.
        answer: The system's answer.

    Returns:
        ``"not_text_graded"``, ``"answer_error"``, ``"synthesis_error"``,
        ``"synthesis_refusal"`` (in that order of precedence), or None.
    """
    if not is_text_graded(case):
        return "not_text_graded"
    if answer.error:
        return "answer_error"
    failure = synthesis_failure(answer.text)
    if failure == "error":
        return "synthesis_error"
    if failure == "refusal":
        return "synthesis_refusal"
    return None


def judge_scores(result: JudgeResult) -> dict[str, float | int | None]:
    """The ``CaseGrade.judge_scores`` dict of a judge result (four ``None``s on error)."""
    if result.error is not None:
        return dict.fromkeys(_SCORE_KEYS)
    return {key: getattr(result, key) for key in _SCORE_KEYS}


def check_judge_family(judge_model: str, generator_profile: str) -> None:
    """Refuses a judge from the family of any model of the generator profile.

    Args:
        judge_model: The judge model id.
        generator_profile: The LLM profile the system under test generates with.

    Raises:
        ValueError: Same family, or an unknown profile or model.
    """
    # LlmConfigError (an unknown profile) is a ValueError.
    settings = load_committed_settings(profile=generator_profile)
    generator_families = {settings.provider_for(m) for m in settings.tier_models().values()}
    family = model_family(judge_model)
    if family in generator_families:
        raise ValueError(
            f"judge {judge_model!r} is {family!r}, a family the generator profile "
            f"{generator_profile!r} uses; the judge must come from another model family"
        )


def _is_judge_error(scores: Mapping[str, Any]) -> bool:
    return all(scores.get(key) is None for key in _SCORE_KEYS)


def judge_summary(report: RunReport, calibration: Sequence[CalibrationReport]) -> dict:
    """The run's judge summary; means only when the judge version is calibrated.

    Args:
        report: The graded run.
        calibration: Calibration reports; the gate is ``scores_reportable``.

    Returns:
        ``judge_version``, ``reportable``, ``status``, ``n_judged``, ``n_judge_error``,
        ``judge_error_case_ids`` and ``means``.
    """
    version = (report.config or {}).get("judge_version") or None
    reportable = version is not None and scores_reportable(version, calibration)
    if version is None:
        status = "none"
    else:
        status = "calibrated" if reportable else "uncalibrated"

    judged = [r for r in report.results if r.grade.judge_scores is not None]
    errors = sorted(r.case.id for r in judged if _is_judge_error(r.grade.judge_scores or {}))
    scored = [r.grade.judge_scores or {} for r in judged if r.case.id not in set(errors)]

    means: dict[str, float | None] | None = None
    if reportable:
        means = {}
        for key in _SCORE_KEYS:
            values = [float(s[key]) for s in scored if s.get(key) is not None]
            means[key] = sum(values) / len(values) if values else None
    return {
        "judge_version": version,
        "reportable": reportable,
        "status": status,
        "n_judged": len(judged),
        "n_judge_error": len(errors),
        "judge_error_case_ids": errors,
        "means": means,
    }


class _GradedRunner(EvalRunner):
    """An ``EvalRunner`` that judges each text-graded case and keeps the run's answers."""

    def __init__(self, config: RunnerConfig | None, judge: ClaimJudge | None) -> None:
        super().__init__(config)
        self._judge = judge
        self._lock = threading.Lock()
        # Per system name: its (case, answer) pairs, for the key-free pass. The judge's
        # error and cost live on each CaseResult, so they survive a checkpoint.
        self.pairs: dict[str, list[tuple[EvalCase, SystemAnswer]]] = {}

    def _after_grade(
        self,
        case: EvalCase,
        answer: SystemAnswer,
        result: CaseResult,
        budget: _Budget,
        system: SystemUnderTest,
    ) -> None:
        """Judges the case in its own task and charges the judge's cost to the budget.

        A skipped text case (an error or a fallback answer) makes no call, costs nothing and
        keeps its reason on the result (docs/specs/grader-v9-number-reading.md, req. 5).
        """
        name = getattr(system, "name", type(system).__name__)
        with self._lock:
            self.pairs.setdefault(name, []).append((case, answer))
        if self._judge is None:
            return
        reason = judge_skip_reason(case, answer)
        if reason is not None:
            if reason != "not_text_graded":
                result.judge_skipped = reason
            return
        # The gateway records each call's cost (configs/llm/pricing.yaml) on the active
        # trace, and enforces the run budget only under one; a RunHalt propagates.
        with trace_query(f"[eval judge] {case.id}") as trace:
            judged = judge_case(self._judge, case, answer)
        cost = float(trace.total_cost_usd)
        budget.charge(cost)
        result.judge_cost_usd = cost
        if judged is not None:
            result.judge_error = judged.error
            result.grade.judge_scores = judge_scores(judged)


def run_graded(
    cases: Sequence[EvalCase],
    system: SystemUnderTest,
    *,
    judge: ClaimJudge | None = None,
    generator_profile: str | None = None,
    calibration: Sequence[CalibrationReport] = (),
    metamorphic: bool = True,
    config: RunnerConfig | None = None,
    **run_kwargs: Any,
) -> RunReport:
    """Runs and grades a suite, judging text cases and running the metamorphic pass.

    Args:
        cases: The cases to run.
        system: The system under test.
        judge: The claim judge; ``None`` runs without judging.
        generator_profile: The system's LLM profile; required with a judge.
        calibration: Calibration reports for the judge summary.
        metamorphic: Whether to run the key-free metamorphic pass.
        config: The runner configuration (workers, cost cap, ...).
        **run_kwargs: Passed on as to ``EvalRunner.run``.

    Returns:
        The run report, with the judge and key-free results in its config.

    Raises:
        ValueError: A judge without a profile, or from the profile's family; raised
            before any call.
        RunHalt: When the judge's budget or circuit breaker stops the run.
    """
    if "run_id" in run_kwargs:  # EvalRunner.run's name for it
        run_kwargs["run_ids"] = [run_kwargs.pop("run_id")]
    return run_graded_many(
        cases,
        [system],
        judge=judge,
        generator_profile=generator_profile,
        calibration=calibration,
        metamorphic=metamorphic,
        config=config,
        **run_kwargs,
    )[0]


def run_graded_many(
    cases: Sequence[EvalCase],
    systems: Sequence[SystemUnderTest],
    *,
    judge: ClaimJudge | None = None,
    generator_profile: str | None = None,
    calibration: Sequence[CalibrationReport] = (),
    metamorphic: bool = True,
    config: RunnerConfig | None = None,
    **run_kwargs: Any,
) -> list[RunReport]:
    """:func:`run_graded` for several systems in one schedule (``EvalRunner.run_many``).

    Args:
        cases: The cases to run.
        systems: The systems under test, with distinct names.
        judge: The claim judge; ``None`` runs without judging.
        generator_profile: The systems' LLM profile; required with a judge.
        calibration: Calibration reports for the judge summary.
        metamorphic: Whether to run the key-free metamorphic pass.
        config: The runner configuration; its cost cap covers all the systems.
        **run_kwargs: Passed on to ``EvalRunner.run_many``.

    Returns:
        One report per system, in the order of ``systems``, each with its own judge and
        key-free results.

    Raises:
        ValueError: A judge without a profile, or from the profile's family; raised
            before any call.
        RunHalt: When the judge's budget or circuit breaker stops the run.
    """
    check_judge(judge, generator_profile)
    runner = _GradedRunner(config, judge)
    reports = runner.run_many(cases, systems, **run_kwargs)
    finish_graded(reports, runner, cases, judge=judge, calibration=calibration,
                  metamorphic=metamorphic)
    return reports


def check_judge(judge: ClaimJudge | None, generator_profile: str | None) -> None:
    """Refuses a judge without a generator profile, or from the profile's family.

    Args:
        judge: The claim judge, or None (nothing to check).
        generator_profile: The systems' LLM profile.

    Raises:
        ValueError: A judge without a profile, or from the profile's family.
    """
    if judge is None:
        return
    if generator_profile is None:
        raise ValueError("a judge needs the generator profile, to check its family")
    check_judge_family(judge.model, generator_profile)


def graded_runner(config: RunnerConfig | None, judge: ClaimJudge | None) -> EvalRunner:
    """The runner ``run_graded_many`` uses: it judges each text case in the case's task.

    Args:
        config: The runner configuration.
        judge: The claim judge, or None.

    Returns:
        The runner, to pass to :func:`finish_graded` after the run.
    """
    return _GradedRunner(config, judge)


def finish_graded(
    reports: Sequence[RunReport],
    runner: EvalRunner,
    cases: Sequence[EvalCase],
    *,
    judge: ClaimJudge | None,
    calibration: Sequence[CalibrationReport] = (),
    metamorphic: bool = False,
) -> None:
    """Adds the judge's totals and summary, and the key-free pass, to each report.

    The judge's errors, skips and cost are summed over the report's results, restored ones
    included (docs/specs/eval-run-checkpoint.md, requirement 10;
    docs/specs/grader-v9-number-reading.md, requirement 5).

    Args:
        reports: The run's reports.
        runner: The runner that produced them (:func:`graded_runner`).
        cases: The run's cases, in suite order.
        judge: The claim judge, or None.
        calibration: Calibration reports for the judge summary.
        metamorphic: Whether to run the key-free metamorphic pass over the answers.
    """
    pairs_by_system = runner.pairs if isinstance(runner, _GradedRunner) else {}
    # Case order, not completion order, so the pass reads the same for any workers.
    order = {c.id: i for i, c in enumerate(cases)}
    for report in reports:
        if judge is not None:
            report.config["judge_version"] = judge.judge_version
            report.config["judge_errors"] = {
                r.case.id: r.judge_error
                for r in sorted(report.results, key=lambda r: r.case.id)
                if r.judge_error is not None
            }
            report.config["judge_skipped"] = {
                r.case.id: r.judge_skipped
                for r in sorted(report.results, key=lambda r: r.case.id)
                if r.judge_skipped is not None
            }
            report.config["judge_cost_usd"] = sum(
                (r.judge_cost_usd for r in report.results), 0.0
            )
        report.config["judge_summary"] = judge_summary(report, calibration)
        if metamorphic:
            pairs = sorted(
                pairs_by_system.get(report.system, []),
                key=lambda p: order.get(p[0].id, len(order)),
            )
            report.config["keyfree"] = metamorphic_pass(pairs)
