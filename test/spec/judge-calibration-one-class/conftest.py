"""Builders for the judge-calibration-one-class spec tests.

Spec: docs/specs/judge-calibration-one-class.md. Nothing is faked: there is no LLM call,
the judge's labels are built by hand as ``JudgeLabels``. Fixtures are session-scoped
factories so hypothesis-driven tests can use them. Everything is synthetic.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.eval.core.graders.judge import Claim
from src.eval.core.graders.judge_calibration import (
    CalibrationAnswer,
    JudgeLabels,
    calibrate,
)

GENERATOR_MODEL = "deepseek-flash"
VERSION = "v1:claude-haiku-5-5"
DIMS = ("correct", "supported", "covered")
_ITEM = {"correct": "s0", "supported": "s0", "covered": "kp0"}


def pairs(tt: int, tf: int, ft: int, ff: int) -> list[tuple[bool, bool]]:
    """(judge, human) label pairs of a 2x2 table, the judge's label first."""
    return (
        [(True, True)] * tt + [(True, False)] * tf + [(False, True)] * ft + [(False, False)] * ff
    )


def make_answer(answer_id: str, *, cited: bool = True) -> CalibrationAnswer:
    """A dev answer with one claim (cited or not) and one key point."""
    return CalibrationAnswer(
        answer_id=answer_id,
        case_id=f"case-{answer_id}",
        split="dev",
        generator_model=GENERATOR_MODEL,
        question="What happened to revenue at AAA?",
        reference_answer="Revenue grew 5%.",
        key_points=["Revenue grew"],
        claims=[Claim("s0", "Revenue grew.", ["Revenue grew 5%."] if cited else [])],
    )


def build(
    correct: list[tuple[bool, bool]],
    supported: list[tuple[bool, bool]],
    covered: list[tuple[bool, bool]],
    *,
    version: str = VERSION,
    errors: frozenset[int] = frozenset(),
    cost: float = 0.01,
) -> tuple[list[CalibrationAnswer], dict, list[JudgeLabels]]:
    """Answers and both raters' labels from per-dimension (judge, human) pair lists.

    Answer i takes item i of ``correct`` and ``covered``; its claim is cited (so it has a
    ``supported`` label) only while i < len(supported). Answers whose index is in
    ``errors`` get a judge_error and no judge labels.
    """
    n = len(correct)
    if len(covered) != n or len(supported) > n:
        raise ValueError("correct and covered must be as long as the answers")
    answers, human, judged = [], {}, []
    for i in range(n):
        aid = f"a{i:03d}"
        cited = i < len(supported)
        answers.append(make_answer(aid, cited=cited))
        dims = {"correct": correct[i], "covered": covered[i]}
        if cited:
            dims["supported"] = supported[i]
        labels = {}
        for dim, (j, h) in dims.items():
            human[(aid, dim, _ITEM[dim])] = h
            labels[(aid, dim, _ITEM[dim])] = j
        if i in errors:
            judged.append(JudgeLabels(answer_id=aid, judge_version=version, labels={},
                                      error="judge_error: bad reply", cost_usd=cost))
        else:
            judged.append(JudgeLabels(answer_id=aid, judge_version=version, labels=labels,
                                      error=None, cost_usd=cost))
    return answers, human, judged


def run_calibrate(answers, human, judged, **kwargs: Any):
    """``calibrate`` with the spec's keywords; a missing keyword fails the test.

    Against code without the new keywords (``min_items``, ``one_class_share``,
    ``agreement_bound``) ``calibrate`` raises TypeError; that is the change not being made
    yet, so it is reported as a test failure, not an error.
    """
    try:
        return calibrate(answers, human, judged, **kwargs)
    except TypeError as exc:
        pytest.fail(f"calibrate does not take the spec's keywords {sorted(kwargs)}: {exc}")


@pytest.fixture(scope="session")
def kit():
    """The builders above, as attributes (tests cannot import conftest)."""
    return SimpleNamespace(
        VERSION=VERSION,
        DIMS=DIMS,
        pairs=pairs,
        build=build,
        make_answer=make_answer,
        calibrate=run_calibrate,
    )
