"""Calibrating the text judge against human labels.

A judge version's scores are reported only after it agrees with a person on the same
answers. This module defines what a label is (one boolean per claim ``correct``, per cited
claim ``supported`` and per key point ``covered``), builds the label sheet a maintainer
fills in from dev answers only, reads it back strictly, collects a candidate judge's
labels from one call per answer, measures Cohen's kappa per dimension with an approximate
interval, chooses the cheapest passing judge outside the generator's families, and writes
a hashed calibration record that a run loads as ``CalibrationReport``s.

A dimension whose human labels are nearly all one class is gated on observed agreement
instead of kappa, which is 0 or undefined there whatever the judge does.

Spec: [docs/specs/judge-calibration.md](../../../../docs/specs/judge-calibration.md),
[judge-calibration-one-class](../../../../docs/specs/judge-calibration-one-class.md).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from fractions import Fraction
from typing import Any

from src.eval.core.case import EvalCase
from src.eval.core.graders.judge import CalibrationReport, Claim, ClaimJudge, model_family
from src.eval.core.judge_wiring import (
    answer_claims,
    case_key_points,
    check_judge_family,
    is_text_graded,
)
from src.eval.core.run_columns import LEGACY_SPLIT, slice_fields_from_tags
from src.eval.core.system import SystemAnswer
from src.llm.profiles import load_committed_settings
from src.observability.trace_context import trace_query

# The verdict kinds a person labels, in report order (requirement 3).
DIMENSIONS: tuple[str, ...] = ("correct", "supported", "covered")
SHEET_HEADER: tuple[str, ...] = ("answer_id", "dimension", "item_id", "text", "label")
RECORD_SCHEMA = "judge-calibration/2"
# The statistic that gates a dimension (judge-calibration-one-class requirement 2).
GATES: tuple[str, ...] = ("kappa", "agreement")
CALIBRATION_SPLIT = "dev"

_JUDGE_ERROR = "judge_error: "
_TRUE_LABELS = frozenset({"y", "yes"})
_FALSE_LABELS = frozenset({"n", "no"})
# Two-sided 95% normal quantile for Cohen's (1960) approximate interval.
_Z95 = 1.96



class CalibrationAnswer:
    """One dev answer to be labelled by a person and by candidate judges.

    Attributes:
        answer_id: Unique within a calibration set.
        case_id: The suite case the answer is for.
        split: Always ``"dev"``.
        generator_model: The model that generated the answer.
        question: The case's question.
        reference_answer: The case's ``expected_text``.
        key_points: The reference's key points, as a tuple.
        claims: The answer's judge claims, as a tuple.
    """

    def __init__(
        self,
        *,
        answer_id: str,
        case_id: str,
        split: str,
        generator_model: str,
        question: str,
        reference_answer: str,
        key_points: Sequence[str],
        claims: Sequence[Claim],
    ) -> None:
        """Builds and validates a calibration answer (requirement 1).

        Raises:
            ValueError: A split other than ``dev``, a blank answer id, no or a blank key
                point, no claims, duplicate claim ids, or a generator model without a
                row in ``configs/llm/llm.yaml``.
        """
        if split != CALIBRATION_SPLIT:
            raise ValueError(
                f"calibration answers come from the dev split only; got split {split!r}"
            )
        if not isinstance(answer_id, str) or not answer_id.strip():
            raise ValueError("answer_id must not be blank")
        points = tuple(key_points)
        if not points:
            raise ValueError(f"answer {answer_id!r}: key_points must not be empty")
        if any(not isinstance(p, str) or not p.strip() for p in points):
            raise ValueError(f"answer {answer_id!r}: a key point is blank")
        claim_tuple = tuple(claims)
        if not claim_tuple:
            raise ValueError(f"answer {answer_id!r} has no claims, so nothing to calibrate")
        counts = Counter(c.claim_id for c in claim_tuple)
        duplicates = sorted(cid for cid, n in counts.items() if n > 1)
        if duplicates:
            raise ValueError(f"answer {answer_id!r}: duplicate claim ids {duplicates}")
        model_family(generator_model)  # raises ValueError for an unknown model

        self.answer_id = answer_id
        self.case_id = case_id
        self.split = split
        self.generator_model = generator_model
        self.question = question
        self.reference_answer = reference_answer
        self.key_points = points
        self.claims = claim_tuple

    def __repr__(self) -> str:
        return (
            f"CalibrationAnswer(answer_id={self.answer_id!r}, case_id={self.case_id!r}, "
            f"generator_model={self.generator_model!r}, claims={len(self.claims)}, "
            f"key_points={len(self.key_points)})"
        )


def calibration_answer(
    case: EvalCase, answer: SystemAnswer, *, answer_id: str, generator_model: str
) -> CalibrationAnswer:
    """A calibration answer from a dev text case and a system's answer (requirement 2).

    Args:
        case: A judge-graded suite case (``expected_text`` and key points).
        answer: The system's answer to it.
        answer_id: The id the answer gets in the calibration set.
        generator_model: The model that generated the answer.

    Returns:
        The calibration answer, with the claims the judge would see in a run.

    Raises:
        ValueError: The case is not text-graded, the answer has an error, or a
            ``CalibrationAnswer`` check fails (non-dev split, no claims, ...).
    """
    if not is_text_graded(case):
        raise ValueError(f"case {case.id!r} is not text-graded (expected_text + key_points)")
    if answer.error:
        raise ValueError(f"the answer to case {case.id!r} has an error: {answer.error}")
    split = slice_fields_from_tags(case.tags)["split"] or LEGACY_SPLIT
    return CalibrationAnswer(
        answer_id=answer_id,
        case_id=case.id,
        split=split,
        generator_model=generator_model,
        question=case.question,
        reference_answer=str(case.expected_text),
        key_points=case_key_points(case),
        claims=answer_claims(answer),
    )


def _answer_label_rows(answer: CalibrationAnswer) -> list[tuple[tuple[str, str, str], str]]:
    """One answer's label keys with their sheet text, in sheet order (requirements 3-4)."""
    rows: list[tuple[tuple[str, str, str], str]] = []
    for claim in answer.claims:
        rows.append(((answer.answer_id, "correct", claim.claim_id), claim.text))
        if claim.cited_passages:
            rows.append(((answer.answer_id, "supported", claim.claim_id), claim.text))
    for i, point in enumerate(answer.key_points):
        rows.append(((answer.answer_id, "covered", f"kp{i}"), point))
    return rows


def _label_rows(answers: Sequence[CalibrationAnswer]) -> list[tuple[tuple[str, str, str], str]]:
    """Every answer's label rows, in sheet order.

    Raises:
        ValueError: Two answers share an ``answer_id``.
    """
    seen: set[str] = set()
    rows: list[tuple[tuple[str, str, str], str]] = []
    for answer in answers:
        if answer.answer_id in seen:
            raise ValueError(f"duplicate answer_id {answer.answer_id!r}")
        seen.add(answer.answer_id)
        rows.extend(_answer_label_rows(answer))
    return rows


def build_label_sheet(answers: Sequence[CalibrationAnswer]) -> str:
    """The blank CSV label sheet (requirement 4).

    Args:
        answers: The calibration answers, in the order the sheet lists them.

    Returns:
        CSV text: the header, then one row per label key with an empty label.

    Raises:
        ValueError: Two answers share an ``answer_id``.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(SHEET_HEADER)
    for (answer_id, dimension, item_id), text in _label_rows(answers):
        writer.writerow([answer_id, dimension, item_id, text, ""])
    return buf.getvalue()


def render_label_context(answers: Sequence[CalibrationAnswer]) -> str:
    """The Markdown the labeller reads beside the sheet (requirement 5).

    Args:
        answers: The calibration answers, in input order.

    Returns:
        Per answer a ``## <answer_id>`` section with the question, the reference answer,
        each key point as ``kp<i>: <text>`` and each claim as ``<claim_id>: <text>``
        followed by its cited passages (or ``(no cited passages)``), all verbatim.
    """
    lines: list[str] = [
        "# Judge calibration: labelling context",
        "",
        "Label each sheet row y or n: `correct` - the claim agrees with the reference "
        "answer; `supported` - the claim's own cited passages support it; `covered` - the "
        "answer covers the key point.",
        "",
    ]
    for answer in answers:
        lines += [f"## {answer.answer_id}", "", f"Case: {answer.case_id}", ""]
        lines += ["Question:", "", answer.question, ""]
        lines += ["Reference answer:", "", answer.reference_answer, ""]
        lines += ["Key points:", ""]
        lines += [f"kp{i}: {point}" for i, point in enumerate(answer.key_points)]
        lines += ["", "Claims:", ""]
        for claim in answer.claims:
            lines += [f"{claim.claim_id}: {claim.text}", ""]
            if not claim.cited_passages:
                lines += ["(no cited passages)", ""]
            for j, passage in enumerate(claim.cited_passages, start=1):
                lines += [f"Passage {j} of {claim.claim_id}:", "", passage, ""]
    return "\n".join(lines) + "\n"


def _parse_label(cell: str, line: int) -> bool:
    """A label cell as a boolean (requirement 6)."""
    value = cell.strip().lower()
    if value in _TRUE_LABELS:
        return True
    if value in _FALSE_LABELS:
        return False
    raise ValueError(f"line {line}: label {cell!r} is not y/yes/n/no")


def parse_label_sheet(
    text: str, answers: Sequence[CalibrationAnswer]
) -> dict[tuple[str, str, str], bool]:
    """Reads a filled label sheet back, strictly (requirement 6).

    Args:
        text: The sheet's text; a leading BOM and ``\\r\\n`` line ends are accepted.
        answers: The answers the sheet was exported for.

    Returns:
        The label of every label key of ``answers``.

    Raises:
        ValueError: A wrong header, a row with the wrong number of cells, a blank or
            unknown label, or a missing, duplicate or unknown key; the message names the
            1-based line number or the missing key.
    """
    expected = {key for key, _ in _label_rows(answers)}
    if text.startswith("﻿"):
        text = text[1:]
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if header is None or tuple(header) != SHEET_HEADER:
        raise ValueError(f"line 1: header must be {list(SHEET_HEADER)}, got {header}")
    labels: dict[tuple[str, str, str], bool] = {}
    for row in reader:
        line = reader.line_num
        if not row:
            continue
        if len(row) != len(SHEET_HEADER):
            raise ValueError(
                f"line {line}: expected {len(SHEET_HEADER)} cells, got {len(row)}"
            )
        key = (row[0], row[1], row[2])
        if key not in expected:
            raise ValueError(f"line {line}: unknown label key {key}")
        if key in labels:
            raise ValueError(f"line {line}: duplicate label key {key}")
        labels[key] = _parse_label(row[4], line)
    missing = sorted(expected - set(labels))
    if missing:
        raise ValueError(f"label sheet is missing key {missing[0]} ({len(missing)} missing)")
    return labels


class JudgeLabels:
    """One candidate judge's labels for one calibration answer.

    Attributes:
        answer_id: The answer labelled.
        judge_version: The judge's version stamp.
        labels: The judge's label per label key; empty on error.
        error: ``"judge_error: ..."`` when the judge could not label the answer.
        cost_usd: The cost the gateway recorded for the call.
    """

    def __init__(
        self,
        *,
        answer_id: str,
        judge_version: str,
        labels: Mapping[tuple[str, str, str], bool],
        error: str | None,
        cost_usd: float,
    ) -> None:
        """Builds the labels (requirement 7)."""
        self.answer_id = answer_id
        self.judge_version = judge_version
        self.labels = dict(labels)
        self.error = error
        self.cost_usd = cost_usd

    def __repr__(self) -> str:
        return (
            f"JudgeLabels(answer_id={self.answer_id!r}, judge_version={self.judge_version!r}, "
            f"labels={len(self.labels)}, error={self.error!r}, cost_usd={self.cost_usd!r})"
        )


def judge_answer_labels(judge: ClaimJudge, answer: CalibrationAnswer) -> JudgeLabels:
    """The judge's labels for one answer, from one call (requirement 7).

    Args:
        judge: The candidate judge.
        answer: The calibration answer.

    Returns:
        The judge's label for each of the answer's label keys, or a ``judge_error`` with
        no labels; with the call's recorded cost.

    Raises:
        ValueError: The judge and the answer's generator are from one model family;
            raised before any call.
        RunHalt: When the run's budget or circuit breaker stops the call.
    """
    family = model_family(judge.model)
    if family == model_family(answer.generator_model):
        raise ValueError(
            f"judge {judge.model!r} and generator {answer.generator_model!r} are both "
            f"{family!r}; a judge is never calibrated on its own family's answers"
        )
    # The gateway records the call's cost on the active trace (as the run's judge does).
    with trace_query(f"[judge calibration] {answer.answer_id}") as trace:
        outcome = judge.reply_verdicts(
            answer.question, answer.reference_answer, answer.key_points, answer.claims
        )
    cost = float(trace.total_cost_usd)
    if isinstance(outcome, str):
        return JudgeLabels(
            answer_id=answer.answer_id,
            judge_version=judge.judge_version,
            labels={},
            error=_JUDGE_ERROR + outcome,
            cost_usd=cost,
        )
    verdicts, covered = outcome
    labels: dict[tuple[str, str, str], bool] = {}
    for claim in answer.claims:
        correct, supported = verdicts[claim.claim_id]
        labels[(answer.answer_id, "correct", claim.claim_id)] = correct
        if claim.cited_passages:
            labels[(answer.answer_id, "supported", claim.claim_id)] = supported
    for i, value in enumerate(covered):
        labels[(answer.answer_id, "covered", f"kp{i}")] = value
    return JudgeLabels(
        answer_id=answer.answer_id,
        judge_version=judge.judge_version,
        labels=labels,
        error=None,
        cost_usd=cost,
    )


class DimensionAgreement:
    """Agreement between judge and human on one dimension's labels.

    Attributes:
        n: The number of labelled items.
        p_o: Observed agreement; None when ``n == 0``.
        p_e: Chance agreement; None when ``n == 0``.
        kappa: Cohen's kappa; None when ``n == 0`` or ``p_e == 1``.
        ci_low: Lower end of Cohen's approximate 95% interval, or None.
        ci_high: Upper end of that interval, or None.
    """

    def __init__(
        self,
        *,
        n: int,
        p_o: float | None,
        p_e: float | None,
        kappa: float | None,
        ci_low: float | None,
        ci_high: float | None,
    ) -> None:
        """Builds the agreement (requirement 8)."""
        self.n = n
        self.p_o = p_o
        self.p_e = p_e
        self.kappa = kappa
        self.ci_low = ci_low
        self.ci_high = ci_high

    def __repr__(self) -> str:
        return (
            f"DimensionAgreement(n={self.n}, p_o={self.p_o!r}, p_e={self.p_e!r}, "
            f"kappa={self.kappa!r}, ci=({self.ci_low!r}, {self.ci_high!r}))"
        )


def dimension_agreement(judge: Sequence[bool], human: Sequence[bool]) -> DimensionAgreement:
    """Cohen's kappa and its approximate interval for one dimension (requirement 8).

    kappa = (p_o - p_e) / (1 - p_e) and se = sqrt(p_o (1 - p_o) / (n (1 - p_e)^2))
    (Cohen 1960); the interval is kappa +/- 1.96 se, clipped to [-1, 1]. Computed in
    exact fractions, so the result does not depend on item order.

    Args:
        judge: The judge's labels.
        human: The person's labels of the same items.

    Returns:
        The agreement.

    Raises:
        ValueError: The sequences differ in length.
    """
    n = len(judge)
    if n != len(human):
        raise ValueError(f"label sequences differ in length: {n} vs {len(human)}")
    if n == 0:
        return DimensionAgreement(n=0, p_o=None, p_e=None, kappa=None, ci_low=None, ci_high=None)
    p_o = Fraction(sum(1 for a, b in zip(judge, human, strict=True) if a == b), n)
    counts_j, counts_h = Counter(judge), Counter(human)
    p_e = Fraction(sum(counts_j[k] * counts_h[k] for k in counts_j), n * n)
    if p_e == 1:
        return DimensionAgreement(
            n=n, p_o=float(p_o), p_e=float(p_e), kappa=None, ci_low=None, ci_high=None
        )
    kappa = float((p_o - p_e) / (1 - p_e))
    se = math.sqrt(float(p_o * (1 - p_o) / (n * (1 - p_e) ** 2)))
    return DimensionAgreement(
        n=n,
        p_o=float(p_o),
        p_e=float(p_e),
        kappa=kappa,
        ci_low=max(-1.0, kappa - _Z95 * se),
        ci_high=min(1.0, kappa + _Z95 * se),
    )


def human_majority_share(human: Sequence[bool]) -> float | None:
    """The share of the more frequent human label (judge-calibration-one-class req. 1).

    Args:
        human: The person's labels of one dimension.

    Returns:
        ``max(#True, #False) / len(human)``, or None when ``human`` is empty.
    """
    n = len(human)
    if n == 0:
        return None
    n_true = sum(1 for h in human if h)
    return max(n_true, n - n_true) / n


def dimension_gate(human: Sequence[bool], *, one_class_share: float = 0.99) -> str:
    """Which statistic gates a dimension (judge-calibration-one-class requirement 2).

    Args:
        human: The person's labels of one dimension; the judge's labels are never read.
        one_class_share: The majority share at or above which the dimension counts as one
            class.

    Returns:
        ``"agreement"`` when the human majority share is at least ``one_class_share``,
        else ``"kappa"`` (also for no labels).
    """
    share = human_majority_share(human)
    return "agreement" if share is not None and share >= one_class_share else "kappa"


def dimension_passed(
    agreement: DimensionAgreement,
    gate: str,
    *,
    threshold: float,
    agreement_bound: float,
    min_items: int,
) -> bool:
    """Whether one dimension passes its gate (judge-calibration-one-class requirement 3).

    Args:
        agreement: The dimension's agreement.
        gate: ``"kappa"`` or ``"agreement"``.
        threshold: The kappa a kappa-gated dimension must reach (inclusive).
        agreement_bound: The observed agreement an agreement-gated dimension must reach
            (inclusive).
        min_items: The minimum number of items.

    Returns:
        ``n >= min_items`` and, by gate, ``kappa >= threshold`` (kappa defined) or
        ``p_o >= agreement_bound``.

    Raises:
        ValueError: An unknown gate.
    """
    if gate == "kappa":
        return (
            agreement.n >= min_items
            and agreement.kappa is not None
            and agreement.kappa >= threshold
        )
    if gate == "agreement":
        return (
            agreement.n >= min_items
            and agreement.p_o is not None
            and agreement.p_o >= agreement_bound
        )
    raise ValueError(f"unknown calibration gate {gate!r}; expected one of {GATES}")


class CalibrationResult:
    """One judge version's calibration against the human labels.

    Attributes:
        judge_version: The judge's version stamp.
        model: The judge model (``judge_version`` after its first ``:``).
        family: The model's family.
        n_answers: Answers the judge labelled without error.
        n_judge_errors: Answers left out for a judge_error.
        dimensions: Agreement per dimension (``correct``, ``supported``, ``covered``).
        threshold: The kappa a kappa-gated dimension must reach.
        min_answers: The minimum number of counted answers.
        passed: Whether the judge version passed.
        cost_usd: The measured cost of judging the labelled answers.
        answer_set_sha256: Hash of the calibration answers.
        label_set_sha256: Hash of the human labels.
        min_items: The minimum number of counted items per dimension.
        one_class_share: The human majority share at or above which a dimension is
            agreement-gated.
        agreement_bound: The observed agreement an agreement-gated dimension must reach.
        gates: The gate per dimension, ``"kappa"`` or ``"agreement"``.
        human_majority_share: The human majority share per dimension (None: no labels).
    """

    def __init__(
        self,
        *,
        judge_version: str,
        model: str,
        family: str,
        n_answers: int,
        n_judge_errors: int,
        dimensions: Mapping[str, DimensionAgreement],
        threshold: float,
        min_answers: int,
        passed: bool,
        cost_usd: float,
        answer_set_sha256: str,
        label_set_sha256: str,
        min_items: int | None = None,
        one_class_share: float = 0.99,
        agreement_bound: float = 0.95,
        gates: Mapping[str, str] | None = None,
        human_majority_share: Mapping[str, float | None] | None = None,
    ) -> None:
        """Builds the result (requirement 9; judge-calibration-one-class requirement 5).

        Without ``gates`` every dimension is kappa-gated (the original rule); without
        ``min_items`` it is ``min_answers``.
        """
        self.judge_version = judge_version
        self.model = model
        self.family = family
        self.n_answers = n_answers
        self.n_judge_errors = n_judge_errors
        self.dimensions = dict(dimensions)
        self.threshold = threshold
        self.min_answers = min_answers
        self.passed = passed
        self.cost_usd = cost_usd
        self.answer_set_sha256 = answer_set_sha256
        self.label_set_sha256 = label_set_sha256
        self.min_items = min_answers if min_items is None else min_items
        self.one_class_share = one_class_share
        self.agreement_bound = agreement_bound
        self.gates = dict(gates) if gates is not None else {d: "kappa" for d in self.dimensions}
        self.human_majority_share: dict[str, float | None] = (
            dict(human_majority_share)
            if human_majority_share is not None
            else {d: None for d in self.dimensions}
        )

    def __repr__(self) -> str:
        kappas = {d: a.kappa for d, a in self.dimensions.items()}
        return (
            f"CalibrationResult(judge_version={self.judge_version!r}, "
            f"n_answers={self.n_answers}, n_judge_errors={self.n_judge_errors}, "
            f"kappas={kappas!r}, passed={self.passed}, cost_usd={self.cost_usd!r})"
        )


def calibrate(
    answers: Sequence[CalibrationAnswer],
    human_labels: Mapping[tuple[str, str, str], bool],
    judge_labels: Sequence[JudgeLabels],
    *,
    threshold: float = 0.7,
    min_answers: int = 100,
    min_items: int | None = None,
    one_class_share: float = 0.99,
    agreement_bound: float = 0.95,
) -> CalibrationResult:
    """Measures one judge version against the human labels (requirement 9).

    Each dimension is gated by ``dimension_gate`` of its human labels over every answer
    (judge_error answers included), so the gate never depends on the judge: kappa for a
    dimension with both labels, observed agreement for one that is (nearly) one class
    (judge-calibration-one-class requirement 4).

    Args:
        answers: The calibration answers.
        human_labels: The person's label for every label key of ``answers``.
        judge_labels: One judge version's labels, one per answer.
        threshold: The kappa a kappa-gated dimension must reach (inclusive).
        min_answers: The minimum number of counted answers.
        min_items: The minimum number of counted items per dimension; None means
            ``min_answers``.
        one_class_share: The human majority share at or above which a dimension is
            gated on observed agreement, in ``(0.5, 1]``.
        agreement_bound: The observed agreement an agreement-gated dimension must reach
            (inclusive), in ``[0, 1]``.

    Returns:
        The per-dimension agreement and the pass verdict.

    Raises:
        ValueError: The human labels' keys are not the answers' label keys; not exactly
            one ``JudgeLabels`` per answer; more than one judge version; or a judge's
            labels without error whose keys are not its answer's label keys; or
            ``one_class_share``, ``agreement_bound`` or ``min_items`` out of range.
    """
    if min_items is None:
        min_items = min_answers
    if not 0.5 < one_class_share <= 1:
        raise ValueError(f"one_class_share must be in (0.5, 1]; got {one_class_share}")
    if not 0 <= agreement_bound <= 1:
        raise ValueError(f"agreement_bound must be in [0, 1]; got {agreement_bound}")
    if min_items < 0:
        raise ValueError(f"min_items must not be negative; got {min_items}")
    keys_by_answer: dict[str, set[tuple[str, str, str]]] = {}
    for key, _ in _label_rows(answers):
        keys_by_answer.setdefault(key[0], set()).add(key)
    all_keys = set().union(*keys_by_answer.values()) if keys_by_answer else set()
    if set(human_labels) != all_keys:
        extra = sorted(set(human_labels) - all_keys)[:3]
        missing = sorted(all_keys - set(human_labels))[:3]
        raise ValueError(
            f"human labels do not match the answers' label keys "
            f"(missing e.g. {missing}, unknown e.g. {extra})"
        )
    by_answer: dict[str, JudgeLabels] = {}
    for jl in judge_labels:
        if jl.answer_id in by_answer:
            raise ValueError(f"two judge labels for answer {jl.answer_id!r}")
        by_answer[jl.answer_id] = jl
    if set(by_answer) != set(keys_by_answer):
        raise ValueError("judge labels must cover exactly the calibration answers, once each")
    versions = {jl.judge_version for jl in judge_labels}
    if len(versions) != 1:
        raise ValueError(f"judge labels must come from one judge version; got {sorted(versions)}")
    (version,) = versions
    for jl in judge_labels:
        if jl.error is None and set(jl.labels) != keys_by_answer[jl.answer_id]:
            raise ValueError(f"judge labels of answer {jl.answer_id!r} have the wrong keys")

    counted = sorted(aid for aid, jl in by_answer.items() if jl.error is None)
    pairs: dict[str, tuple[list[bool], list[bool]]] = {d: ([], []) for d in DIMENSIONS}
    for aid in counted:
        for key in sorted(keys_by_answer[aid]):
            judged, human = pairs[key[1]]
            judged.append(by_answer[aid].labels[key])
            human.append(human_labels[key])
    dimensions = {d: dimension_agreement(*pairs[d]) for d in DIMENSIONS}
    # The gate reads every answer's human labels, never the judge's (one-class req. 4).
    human_by_dim: dict[str, list[bool]] = {d: [] for d in DIMENSIONS}
    for key in sorted(all_keys):
        human_by_dim[key[1]].append(human_labels[key])
    gates = {
        d: dimension_gate(human_by_dim[d], one_class_share=one_class_share) for d in DIMENSIONS
    }
    shares = {d: human_majority_share(human_by_dim[d]) for d in DIMENSIONS}
    n_answers = len(counted)
    passed = n_answers >= min_answers and all(
        dimension_passed(
            dimensions[d],
            gates[d],
            threshold=threshold,
            agreement_bound=agreement_bound,
            min_items=min_items,
        )
        for d in DIMENSIONS
    )
    model = version.partition(":")[2]
    return CalibrationResult(
        judge_version=version,
        model=model,
        family=model_family(model),
        n_answers=n_answers,
        n_judge_errors=len(by_answer) - n_answers,
        dimensions=dimensions,
        threshold=threshold,
        min_answers=min_answers,
        passed=passed,
        cost_usd=math.fsum(jl.cost_usd for jl in judge_labels),
        answer_set_sha256=answer_set_sha256(answers),
        label_set_sha256=label_set_sha256(human_labels),
        min_items=min_items,
        one_class_share=one_class_share,
        agreement_bound=agreement_bound,
        gates=gates,
        human_majority_share=shares,
    )


def _min_kappa(kappas: Sequence[float | None]) -> float:
    """The smallest kappa, or NaN when any is undefined (or none is given)."""
    if not kappas or any(k is None for k in kappas):
        return math.nan
    return min(float(k) for k in kappas if k is not None)


def as_report(result: CalibrationResult) -> CalibrationReport:
    """The result as the existing calibration gate's report (requirement 10).

    Args:
        result: A calibration result.

    Returns:
        A report whose ``kappa`` is the smallest kappa over the kappa-gated dimensions
        (NaN when one is undefined or none is kappa-gated) and whose ``passed`` is
        ``result.passed``. An agreement-gated dimension's kappa is left out
        (judge-calibration-one-class requirement 6).
    """
    kappas = [
        result.dimensions[d].kappa if d in result.dimensions else None
        for d in DIMENSIONS
        if result.gates.get(d, "kappa") == "kappa"
    ]
    return CalibrationReport(
        judge_version=result.judge_version,
        n=result.n_answers,
        kappa=_min_kappa(kappas),
        threshold=result.threshold,
        min_items=result.min_answers,
        passed=result.passed,
    )


def choose_judge(
    results: Sequence[CalibrationResult], *, generator_profile: str
) -> CalibrationResult | None:
    """The cheapest passing judge outside the generator's families (requirement 11).

    Args:
        results: Candidates calibrated on one labelled set.
        generator_profile: The LLM profile the system under test generates with.

    Returns:
        The eligible result with the lowest ``cost_usd`` (ties: the smaller
        ``judge_version``), or None when none is eligible.

    Raises:
        ValueError: Results from different answer or label sets, a repeated judge
            version, or an unknown profile.
    """
    load_committed_settings(profile=generator_profile)  # an unknown profile raises
    if len({r.answer_set_sha256 for r in results}) > 1:
        raise ValueError("candidates were calibrated on different answer sets")
    if len({r.label_set_sha256 for r in results}) > 1:
        raise ValueError("candidates were calibrated on different label sets")
    versions = Counter(r.judge_version for r in results)
    repeated = sorted(v for v, n in versions.items() if n > 1)
    if repeated:
        raise ValueError(f"judge versions calibrated more than once: {repeated}")

    eligible = []
    for result in results:
        if not result.passed:
            continue
        model_family(result.model)  # an unknown model is an error, not ineligible
        try:
            check_judge_family(result.model, generator_profile)
        except ValueError:
            continue
        eligible.append(result)
    if not eligible:
        return None
    return min(eligible, key=lambda r: (r.cost_usd, r.judge_version))


def _sha256_json(obj: Any, *, sort_keys: bool = False) -> str:
    """SHA-256 (hex) of the compact JSON of *obj*, UTF-8 encoded."""
    text = json.dumps(obj, separators=(",", ":"), sort_keys=sort_keys)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def label_set_sha256(labels: Mapping[tuple[str, str, str], bool]) -> str:
    """SHA-256 of the canonical label set (requirement 12).

    Args:
        labels: Labels by ``(answer_id, dimension, item_id)``.

    Returns:
        The hash of the sorted ``[answer_id, dimension, item_id, label]`` lists.
    """
    return _sha256_json(sorted([a, d, i, v] for (a, d, i), v in labels.items()))


def answer_set_sha256(answers: Sequence[CalibrationAnswer]) -> str:
    """SHA-256 of the canonical answer set (requirement 12).

    Args:
        answers: The calibration answers, in any order.

    Returns:
        The hash of one object per answer, sorted by ``answer_id``.
    """
    objs = [
        {
            "answer_id": a.answer_id,
            "case_id": a.case_id,
            "split": a.split,
            "generator_model": a.generator_model,
            "question": a.question,
            "reference_answer": a.reference_answer,
            "key_points": list(a.key_points),
            "claims": [
                {"claim_id": c.claim_id, "text": c.text, "cited_passages": list(c.cited_passages)}
                for c in a.claims
            ],
        }
        for a in sorted(answers, key=lambda a: a.answer_id)
    ]
    return _sha256_json(objs, sort_keys=True)


def record_sha256(record: Mapping[str, Any]) -> str:
    """SHA-256 of a calibration record's key-sorted compact JSON (requirement 12)."""
    return _sha256_json(record, sort_keys=True)


def _opt_float(value: float | None) -> float | None:
    return None if value is None else float(value)


def _candidate(result: CalibrationResult) -> dict[str, Any]:
    """One candidate's entry in the record (requirement 13; one-class requirement 7)."""

    def dimension(dim: str, a: DimensionAgreement) -> dict[str, Any]:
        gate = result.gates.get(dim, "kappa")
        passed = dimension_passed(
            a,
            gate,
            threshold=result.threshold,
            agreement_bound=result.agreement_bound,
            min_items=result.min_items,
        )
        return {
            "n": int(a.n),
            "p_o": _opt_float(a.p_o),
            "p_e": _opt_float(a.p_e),
            "kappa": _opt_float(a.kappa),
            "ci_low": _opt_float(a.ci_low),
            "ci_high": _opt_float(a.ci_high),
            "gate": gate,
            "human_majority_share": _opt_float(result.human_majority_share.get(dim)),
            "passed": bool(passed),
        }

    return {
        "judge_version": result.judge_version,
        "model": result.model,
        "family": result.family,
        "n_answers": int(result.n_answers),
        "n_judge_errors": int(result.n_judge_errors),
        "threshold": float(result.threshold),
        "min_answers": int(result.min_answers),
        "min_items": int(result.min_items),
        "one_class_share": float(result.one_class_share),
        "agreement_bound": float(result.agreement_bound),
        "passed": bool(result.passed),
        "cost_usd": float(result.cost_usd),
        "dimensions": {dim: dimension(dim, a) for dim, a in result.dimensions.items()},
    }


def calibration_record(
    results: Sequence[CalibrationResult],
    *,
    answers: Sequence[CalibrationAnswer],
    human_labels: Mapping[tuple[str, str, str], bool],
) -> dict:
    """The versioned calibration record (requirement 13).

    Args:
        results: The candidates' calibration results, all on ``answers``/``human_labels``.
        answers: The calibration answers.
        human_labels: The human labels.

    Returns:
        The record: schema, the two set hashes, counts and the candidates sorted by
        ``judge_version``. It has no timestamp, so equal inputs give equal records.

    Raises:
        ValueError: A result from another answer or label set, or a repeated judge
            version.
    """
    answer_hash = answer_set_sha256(answers)
    label_hash = label_set_sha256(human_labels)
    for result in results:
        if result.answer_set_sha256 != answer_hash:
            raise ValueError(f"{result.judge_version}: calibrated on another answer set")
        if result.label_set_sha256 != label_hash:
            raise ValueError(f"{result.judge_version}: calibrated on another label set")
    versions = Counter(r.judge_version for r in results)
    repeated = sorted(v for v, n in versions.items() if n > 1)
    if repeated:
        raise ValueError(f"judge versions given more than once: {repeated}")
    return {
        "schema": RECORD_SCHEMA,
        "answer_set_sha256": answer_hash,
        "label_set_sha256": label_hash,
        "n_answers": len(answers),
        "n_labels": len(human_labels),
        "candidates": [
            _candidate(r) for r in sorted(results, key=lambda r: r.judge_version)
        ],
    }


def calibration_file_text(record: Mapping[str, Any]) -> str:
    """The committed record file's text (requirement 14).

    Args:
        record: A calibration record.

    Returns:
        Indented, key-sorted JSON of the record and its ``record_sha256``, with a
        trailing newline.
    """
    doc = {"record": record, "record_sha256": record_sha256(record)}
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def load_calibration_reports(text: str) -> list[CalibrationReport]:
    """The gate's reports from a committed record file (requirement 14).

    Args:
        text: The file's text.

    Returns:
        One report per candidate, in record order.

    Raises:
        ValueError: The text is not a record file, the record was edited (its hash does
            not match), or its schema is not ``judge-calibration/2`` (a ``/1`` record was
            decided by another rule and is refused).
    """
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"calibration file is not JSON: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("record"), dict):
        raise ValueError("calibration file has no 'record' object")
    record = doc["record"]
    if doc.get("record_sha256") != record_sha256(record):
        raise ValueError("calibration record does not match its record_sha256 (edited?)")
    if record.get("schema") != RECORD_SCHEMA:
        raise ValueError(
            f"calibration record schema {record.get('schema')!r} is not {RECORD_SCHEMA!r}"
        )
    candidates = record.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("calibration record has no 'candidates' list")
    reports = []
    for cand in candidates:
        dims = cand.get("dimensions") or {}
        for d in dims:
            if (dims[d] or {}).get("gate") not in GATES:
                raise ValueError(f"calibration record dimension {d!r} has no valid 'gate'")
        kappas = [
            dims[d].get("kappa") if d in dims else None
            for d in DIMENSIONS
            if d not in dims or dims[d]["gate"] == "kappa"
        ]
        reports.append(
            CalibrationReport(
                judge_version=cand["judge_version"],
                n=int(cand["n_answers"]),
                kappa=_min_kappa(kappas),
                threshold=float(cand["threshold"]),
                min_items=int(cand["min_answers"]),
                passed=bool(cand["passed"]),
            )
        )
    return reports
