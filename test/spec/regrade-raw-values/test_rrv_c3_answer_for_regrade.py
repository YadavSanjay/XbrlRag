"""C3: the answer a re-grade grades (docs/specs/regrade-raw-values.md, requirement 3).

The real case is int-bd7ee7bd1bce-0231b50a (fixture; dev split, public SEC XBRL values): key
21,244,000,000 is MU's value, the middle of the seven listed rows, and the answer states it as
the median (grading-stated-aggregate C3).
"""

from __future__ import annotations

import dataclasses
import statistics

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.regrade import answer_for_regrade, answer_from_payload
from src.eval.core.system import SystemAnswer

MU = 21_244_000_000.0


@pytest.mark.criterion("C3")
def test_the_real_stored_payload_regrades_correct_from_its_text(kit):
    # oracle: grading-stated-aggregate C3 and requirement 3 by hand — the stored filtered
    # values [7, 8, ...] miss the key, so the hedged answer is HEDGED_MISS; rebuilt from the
    # text, the stated median 21,244,000,000 is kept and is the key: CORRECT
    case = kit.real_case()
    stored = grade_case(case, answer_from_payload(kit.real_payload()))
    regraded = grade_case(case, answer_for_regrade(kit.real_payload(), "xbrlrag_full"))
    assert stored.grade is Grade.HEDGED_MISS
    assert regraded.grade is Grade.CORRECT
    # rel 1e-12: the matched value is the parsed amount itself, no arithmetic
    assert regraded.matched_value == pytest.approx(MU, rel=1e-12)


@pytest.mark.criterion("C3")
def test_every_other_field_is_answer_from_payloads(kit):
    # oracle: requirement 3 by hand — only numeric_values differ: the text states
    # 3,500,000,000 (xbrlrag_full rebuild); every other field is as answer_from_payload builds
    produced = {
        "text": "Revenue was 3,500,000,000 USD.",
        "numeric_values": [1.0],
        "rows": [{"ticker": "AAA", "value": 3.0}],
        "fact_ids": [15488, 15811],
        "chunk_ids": ["c-1"],
        "refused": False,
        "hedged": True,
        "error": "Boom('x')",
        "selection": {"accession": "0000000000-24-000001", "why": "resolved"},
        "agent": {"steps": 3},
    }
    got = answer_for_regrade(produced, "xbrlrag_full")
    base = answer_from_payload(produced)
    assert isinstance(got, SystemAnswer)
    assert got.numeric_values == [3_500_000_000.0]
    assert dataclasses.replace(got, numeric_values=[]) == dataclasses.replace(
        base, numeric_values=[])
    assert produced["numeric_values"] == [1.0]


def _members():
    return st.lists(st.integers(1, 9999), min_size=3, max_size=9, unique=True).map(
        lambda vs: sorted(vs, reverse=True)[: len(vs) - (1 - len(vs) % 2)])


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(values=_members(), pick=st.integers(0, 8), hedged=st.booleans(),
       stated=st.booleans(), listed=st.booleans(),
       system=st.sampled_from(["xbrlrag_full", "naive_rag_filtered", "html_rag", None]))
def test_regrading_a_grades_own_produced_reproduces_it(kit, values, pick, hedged, stated,
                                                      listed, system):
    # oracle: requirement 3 — a grade stores its raw values (requirement 1), so its own
    # produced, re-graded with the same grader, is graded from the same values: same grade,
    # whatever the system name
    vals = [v * 1e6 for v in values]
    tickers = [f"DEV{chr(65 + i)}" for i in range(len(vals))]
    rows = list(zip(tickers, vals, strict=True))
    case = kit.agg_case("screen.median", tickers, statistics.median(vals))
    parts = []
    if stated:
        parts.append(f"The median across the retailers was {int(vals[pick % len(vals)]):,} USD.")
    if listed or not stated:
        parts.append(", ".join(f"{t} had {int(v):,} USD" for t, v in rows) + ".")
    first = grade_case(case, kit.answer(" ".join(parts), rows, hedged=hedged))
    again = grade_case(case, answer_for_regrade(first.produced, system))
    assert again.grade is first.grade
    assert again.value_correct == first.value_correct
    assert again.produced["numeric_values"] == first.produced["numeric_values"]


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(amount=st.integers(1, 10**6).map(lambda v: v * 1000), key=st.integers(1, 10**6),
       hedged=st.booleans(), system=st.sampled_from(["xbrlrag_full", "closed_book"]))
def test_answers_no_filter_touches_grade_alike(kit, amount, key, hedged, system):
    # oracle: requirement 3 — a sum answer with no rows, no dates, ranks or periods: no
    # filter removes its one stated amount, so the stored values equal the text's and
    # answer_for_regrade grades as answer_from_payload does
    text = f"The combined total was {amount:,} USD."
    produced = {"text": text, "numeric_values": [float(amount)], "rows": [], "fact_ids": [1],
                "chunk_ids": [], "refused": False, "hedged": hedged, "error": None}
    case = kit.agg_case("screen.sum", ["DEVA", "DEVB", "DEVC"], float(key * 1000))
    via_regrade = grade_case(case, answer_for_regrade(produced, system))
    via_payload = grade_case(case, answer_from_payload(produced))
    assert via_regrade.grade is via_payload.grade
    assert via_regrade.matched_value == via_payload.matched_value
    assert via_regrade.produced["numeric_values"] == [float(amount)]
