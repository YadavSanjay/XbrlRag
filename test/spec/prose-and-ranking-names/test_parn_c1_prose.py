"""C1: the v2 grader reads a JSON reply's prose, as it reads XbrlRag's (requirement 1).

The reference is the grade of the same answer with ``text`` set to the object's ``"text"``:
an XbrlRag-style answer, which is prose already.
"""

from __future__ import annotations

import dataclasses

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.eval.core.graders.dispatch import grade_case
from src.eval.core.graders.v2 import grade_v2

# Sentences a baseline's other JSON fields hold: statements naming any filer as the highest
# (with or without the ratio's words), and plain figures.
NOISE = [
    "Last year Devc had the highest operating margin of the peers.",
    "DEVD had the highest operating margin in fiscal 2023.",
    "DEVA had the largest revenue.",
    "Devb Corporation is the top payer of dividends.",
    "DEVE led the group on growth.",
    "DEVC's revenue was 300,000,000 USD.",
    "Among the 5 covered companies, DEVA, DEVB and DEVC had the most revenue.",
]


@pytest.mark.criterion("C1")
def test_pick_is_read_from_the_prose(kit):
    # oracle: fixture key — DEVB's 30.0% operating margin is the highest of the top three;
    # the prose says so. The envelope's statements, read first, name DEVC as highest; the
    # grade must be the prose's (requirement 1), which is value-correct (compound-shape C7)
    shape = "rank_by_ratio"
    case = kit.generated(shape)
    rows = kit.revenue_rows(("DEVA", "DEVB", "DEVC", "DEVD"))
    prose = kit.gen_prose(shape)
    plain = kit.gen_answer(shape, rows=rows, cited=False, text=prose)
    wrapped = dataclasses.replace(plain, text=kit.envelope(
        prose, rows, 30.0, statements=[NOISE[0], NOISE[2]], text_last=True))
    assert grade_case(case, plain).value_correct is True
    g = grade_case(case, wrapped)
    assert g.value_correct is True
    assert g.checks["dependent.pick"] is True
    assert kit.outcome(g) == kit.outcome(grade_case(case, plain))


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("shape", ["rank_by_ratio", "leader_growth"])
def test_disclosures_are_read_from_the_prose(kit, shape):
    # oracle: requirement 1 — a cited right answer whose prose does not state its universe
    # is not CORRECT and fails ranking.universe (compound-shape C7's NO_UNIVERSE case); a
    # statement elsewhere in the envelope that states the universe does not change that
    case = kit.generated(shape)
    prose = kit.gen_prose(shape, universe=False)
    plain = kit.gen_answer(shape, universe=False, text=prose)
    wrapped = dataclasses.replace(plain, text=kit.envelope(
        prose, plain.rows, kit.DEPENDENT[shape][1], statements=[NOISE[6]]))
    g = grade_case(case, wrapped)
    assert g.checks["ranking.universe"] is False
    assert kit.outcome(g) == kit.outcome(grade_case(case, plain))


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@settings(max_examples=60)
@given(which=st.sampled_from(["rank_by_ratio", "leader_growth", "hx"]),
       right=st.booleans(), cited=st.booleans(), universe=st.booleans(),
       statements=st.lists(st.sampled_from(NOISE), max_size=4),
       text_last=st.booleans())
def test_wrapping_the_prose_never_changes_the_grade(kit, which, right, cited, universe,
                                                    statements, text_last):
    # oracle: requirement 1 — the grade of an answer whose text is a JSON object equals the
    # grade of the same answer with text = the object's "text"; the noise sentences and the
    # key order are arbitrary. The pick reader must also find DEVB in the right prose.
    if which == "hx":
        case = kit.hx_compound()
        plain = kit.hx_answer(fact_ids=kit.HX_FACT_IDS if cited else [])
        value = 300 * kit.M
        grade = grade_v2
    else:
        case = kit.generated(which)
        plain = kit.gen_answer(which, right=right, cited=cited, universe=universe)
        value = kit.DEPENDENT[which][1 if right else 3]
        grade = grade_case
    wrapped = dataclasses.replace(plain, text=kit.envelope(
        plain.text, plain.rows, value, statements=statements, text_last=text_last))
    assert kit.outcome(grade(case, wrapped)) == kit.outcome(grade(case, plain))
